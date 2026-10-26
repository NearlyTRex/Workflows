"""Package a UWP app's staged files as a signed MSIX, for Xbox Dev Mode or sideloading.

On PATH as ci-msix after build-env (see build_env.py). For Windows runners set up by cpp-build's
msvc-uwp, which puts makeappx, signtool, dumpbin and lib on PATH.

Usage: ci-msix --dir STAGE --manifest Package.appxmanifest --out APP.msix [--version X.Y.Z[.W]]
               [--publisher "CN=..."] [--vclibs] [--check-imports] [--allow-import DLL...]
               [--certificate PFX]

The manifest is written into STAGE as AppxManifest.xml, with its Identity's Version and Publisher
replaced when --version and --publisher are given. Its Executable, and every image it names (as
is, or as a scale- or targetsize-qualified variant), must be in STAGE. --vclibs copies the C++
runtime DLLs a UWP app needs (vcruntime140_app.dll and the rest) into STAGE from the Windows SDK's
Microsoft.VCLibs framework package, for a device that doesn't have that package installed.
--check-imports fails when an .exe or .dll in STAGE imports a DLL that is neither in STAGE nor
one WindowsApp.lib or ucrt.lib links to, the system's, which a UWP app otherwise only shows by
dying at launch.

The package is packed with makeappx and signed with signtool: by --certificate, a PFX whose
password is MSIX_CERTIFICATE_PASSWORD and whose subject is the manifest's Publisher, or else by a
new self-signed certificate for that Publisher, exported beside the package as APP.cer for the
device to trust.
"""

import argparse
import os
import re
import secrets
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

IMAGE = re.compile(r"\.(png|jpe?g|gif|bmp)$", re.IGNORECASE)
IDENTITY = re.compile(r"<Identity\b[^>]*>", re.DOTALL)
VCLIBS = Path("Microsoft SDKs", "Windows Kits", "10", "ExtensionSDKs", "Microsoft.VCLibs")
# signtool's error when the certificate's subject isn't the manifest's Publisher.
PUBLISHER_MISMATCH = "0x8007000B"

# A certificate for code signing only (the EKU), that is no certificate authority.
SELF_SIGNED = """
$cert = New-SelfSignedCertificate -Type Custom -Subject $env:SUBJECT -KeyUsage DigitalSignature `
  -FriendlyName 'ci-msix' -CertStoreLocation Cert:\\CurrentUser\\My `
  -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
$password = ConvertTo-SecureString $env:PASSWORD -AsPlainText -Force
Export-PfxCertificate -Cert $cert -FilePath $env:PFX -Password $password | Out-Null
Export-Certificate -Cert $cert -FilePath $env:CER | Out-Null
Remove-Item $cert.PSPath
"""


class ToolError(Exception):
    """A step that failed, with what to print."""


def four_part(version):
    """version as the four dot-separated numbers below 65536 a package version is."""
    parts = version.removeprefix("v").split(".")
    parts += ["0"] * (4 - len(parts))
    if len(parts) != 4 or not all(part.isdigit() and int(part) < 65536 for part in parts):
        raise argparse.ArgumentTypeError(f"not a package version (up to four numbers below 65536): {version!r}")
    return ".".join(str(int(part)) for part in parts)


def parse_args(argv):
    """The command line, with --version made four numbers."""
    parser = argparse.ArgumentParser(prog="ci-msix", description="Package a UWP app as a signed MSIX.")
    parser.add_argument("--dir", required=True, type=Path, help="the staged files, as the package holds them")
    parser.add_argument("--manifest", required=True, type=Path, help="the Package.appxmanifest to package")
    parser.add_argument("--out", required=True, type=Path, help="the .msix to write")
    parser.add_argument("--version", type=four_part, help="the Identity Version to package with")
    parser.add_argument("--publisher", help="the Identity Publisher to package with, e.g. CN=Someone")
    parser.add_argument("--vclibs", action="store_true", help="copy the UWP C++ runtime DLLs into the package")
    parser.add_argument("--check-imports", action="store_true", help="fail on DLL imports nothing provides")
    parser.add_argument("--allow-import", action="append", default=[], metavar="DLL",
                        help="a DLL the device provides that neither WindowsApp.lib nor ucrt.lib links to")
    parser.add_argument("--certificate", type=Path, help="a PFX to sign with; otherwise a self-signed one")
    return parser.parse_args(argv)


def attribute(value):
    """value quoted for an XML attribute."""
    return '"' + value.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;") + '"'


def write_manifest(source, stage, version, publisher):
    """Write source into stage as AppxManifest.xml with Identity's fields set; it, parsed."""
    text = source.read_text(encoding="utf-8-sig")
    identity = IDENTITY.search(text)
    if identity is None:
        raise ToolError(f"{source} has no <Identity> element")
    tag = identity.group(0)
    for name, value in (("Version", version), ("Publisher", publisher)):
        if value:
            tag, count = re.subn(rf'\b{name}\s*=\s*("[^"]*"|\'[^\']*\')', f"{name}={attribute(value)}", tag)
            if not count:
                raise ToolError(f"{source}'s <Identity> has no {name} to set")
    text = text[:identity.start()] + tag + text[identity.end():]
    (stage / "AppxManifest.xml").write_text(text, encoding="utf-8")
    return ET.fromstring(text)


def local(name):
    """An XML name without its namespace."""
    return name.rpartition("}")[2]


def present(stage, value, qualified):
    """Whether stage has the file value names, or with qualified, a qualified variant of it."""
    path = stage / value.replace("\\", "/")
    return path.is_file() or (qualified and any(path.parent.glob(f"{path.stem}.*{path.suffix}")))


def values(manifest):
    """Each attribute and text in the manifest, as its local name and value."""
    for element in manifest.iter():
        yield from ((local(name), value) for name, value in element.attrib.items())
        # <Logo> holds its image as text.
        yield local(element.tag), (element.text or "").strip()


def missing_files(stage, manifest):
    """The manifest's Executable and images that stage doesn't have, by their manifest paths."""
    return [value for name, value in values(manifest)
            if (name == "Executable" and not present(stage, value, False))
            or (IMAGE.search(value) and not present(stage, value, True))]


def identity(manifest):
    """The manifest's Identity element."""
    return next(element for element in manifest.iter() if local(element.tag) == "Identity")


def bundle_vclibs(stage, architecture, env):
    """Copy the VCLibs framework package's DLLs for architecture into stage; their names."""
    root = Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), VCLIBS)
    found = sorted(root.glob(f"*/Appx/Retail/{architecture}/Microsoft.VCLibs.{architecture}.*.appx"))
    if not found:
        raise ToolError(f"no Microsoft.VCLibs package for {architecture} under {root}")
    with zipfile.ZipFile(found[-1]) as package:
        names = [name for name in package.namelist() if "/" not in name and name.lower().endswith(".dll")]
        for name in names:
            (stage / name).write_bytes(package.read(name))
    return names


def tool(run, *command, env=None):
    """command's output, which it must exit 0 with; ToolError naming it otherwise."""
    try:
        result = run(list(command), capture_output=True, text=True, env=env)
    except FileNotFoundError:
        raise ToolError(f"{command[0]} isn't on PATH; cpp-build's msvc-uwp puts it there") from None
    if result.returncode != 0:
        raise ToolError(f"{command[0]} {command[1]} failed ({result.returncode}):\n{result.stdout}{result.stderr}")
    return result.stdout


def dll_names(output):
    """The DLL names a dumpbin or lib listing gives, one per line, in lower case."""
    # Headers such as "Dump of file C:\\app\\x.dll" have spaces; names don't, though lib may
    # list one with the path it was built at.
    lines = (line.strip().lower() for line in output.splitlines())
    return {line.rpartition("\\")[2] for line in lines if line.endswith(".dll") and " " not in line}


# What every UWP device provides: the Windows API, and the Universal CRT's API
# sets (api-ms-win-crt-*), which the store CRT links through ucrt.lib.
SYSTEM_LIBRARIES = ("WindowsApp.lib", "ucrt.lib")


def system_dlls(run, env):
    """The DLLs SYSTEM_LIBRARIES link to, found in LIB."""
    directories = [directory for directory in env.get("LIB", "").split(";") if directory]
    names = set()
    for name in SYSTEM_LIBRARIES:
        library = next((Path(d, name) for d in directories if Path(d, name).is_file()), None)
        if library is None:
            raise ToolError(f"no {name} in LIB; cpp-build's msvc-uwp sets LIB")
        names |= dll_names(tool(run, "lib", "/nologo", "/list", str(library)))
    return names


def unprovided_imports(stage, run, env, allowed):
    """Each binary in stage's imports that nothing provides, by the binary's path in stage."""
    binaries = [path for path in sorted(stage.rglob("*")) if path.suffix.lower() in (".exe", ".dll")]
    provided = {path.name.lower() for path in binaries} | system_dlls(run, env) | {name.lower() for name in allowed}
    problems = {}
    for binary in binaries:
        imports = dll_names(tool(run, "dumpbin", "/nologo", "/dependents", str(binary))) - {binary.name.lower()}
        unprovided = sorted(imports - provided)
        if unprovided:
            problems[binary.relative_to(stage).as_posix()] = unprovided
    return problems


def self_signed(subject, work, out, run, env):
    """A new code-signing certificate for subject, as a PFX in work and its .cer beside out."""
    pfx, cer, password = work / "ci-msix.pfx", out.with_suffix(".cer"), secrets.token_urlsafe(24)
    tool(run, "pwsh", "-NoProfile", "-NonInteractive", "-Command", SELF_SIGNED,
         env={**env, "SUBJECT": subject, "PASSWORD": password, "PFX": str(pfx), "CER": str(cer)})
    return pfx, password, cer


def sign(package, pfx, password, run):
    """Sign package with pfx, saying when its subject isn't the manifest's Publisher."""
    try:
        tool(run, "signtool", "sign", "/fd", "SHA256", "/f", str(pfx), "/p", password, str(package))
    except ToolError as error:
        if PUBLISHER_MISMATCH in str(error):
            raise ToolError(f"{error}\nThe manifest's Publisher must be the certificate's subject.") from None
        raise


def package(args, run, env, work):
    """Stage, check, pack and sign; the lines that say what was made."""
    manifest = write_manifest(args.manifest, args.dir, args.version, args.publisher)
    made = []
    if args.vclibs:
        names = bundle_vclibs(args.dir, identity(manifest).get("ProcessorArchitecture", "x64"), env)
        made.append(f"VC++ runtime: {', '.join(names)}")
    missing = missing_files(args.dir, manifest)
    if missing:
        raise ToolError(f"{args.dir} lacks what the manifest names: {', '.join(missing)}")
    if args.check_imports:
        problems = unprovided_imports(args.dir, run, env, args.allow_import)
        if problems:
            raise ToolError("imports neither packaged nor the system's:\n" + "\n".join(
                f"  {binary}: {', '.join(names)}" for binary, names in problems.items()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tool(run, "makeappx", "pack", "/o", "/d", str(args.dir), "/p", str(args.out))
    if args.certificate:
        sign(args.out, args.certificate, env.get("MSIX_CERTIFICATE_PASSWORD", ""), run)
    else:
        pfx, password, cer = self_signed(identity(manifest).get("Publisher"), work, args.out, run, env)
        sign(args.out, pfx, password, run)
        made.append(f"Certificate: {cer}")
    return [f"Package: {args.out} ({identity(manifest).get('Version')})", *made]


def main(argv=None, run=subprocess.run, env=None):
    """Make the package; 1, with an error annotation, when a step fails."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ) if env is None else env
    try:
        with tempfile.TemporaryDirectory() as work:
            lines = package(args, run, env, Path(work))
    except ToolError as error:
        print(f"::error::ci-msix: {error}")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
