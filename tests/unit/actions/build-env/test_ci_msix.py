"""ci_msix.py, with the Windows SDK's tools stood in for by a recorder."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from script_helpers import load_script

ci_msix = load_script("actions/build-env/ci_msix.py")

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" IgnorableNamespaces="uap">
  <Identity Name="Some.App" Publisher="CN=Placeholder" Version="0.0.0.0" ProcessorArchitecture="x64" />
  <Properties><DisplayName>Some App</DisplayName><Logo>Assets\\StoreLogo.png</Logo></Properties>
  <Applications>
    <Application Id="App" Executable="app.exe" EntryPoint="Some.App">
      <uap:VisualElements DisplayName="Some App" Description="d" BackgroundColor="#000000"
        Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:SplashScreen Image="Assets\\SplashScreen.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
</Package>
"""
IMAGES = ["StoreLogo.png", "Square150x150Logo.png", "Square44x44Logo.scale-200.png", "SplashScreen.png"]
WINDOWS_APP = "Microsoft (R) Library Manager\napi-ms-win-core-synch-l1-1-0.dll\nd:\\os\\obj\\d3d11.dll\nkernel32.dll\n"


class Tools:
    """Stands in for subprocess.run: records each command and answers as the SDK's tools would."""

    def __init__(self, imports=None, fail=None):
        self.commands, self.envs = [], []
        self.imports = imports or {}
        self.fail = fail or {}

    def __call__(self, command, capture_output, text, env):
        assert capture_output and text
        self.commands.append(command)
        self.envs.append(env)
        name = command[0]
        if name in self.fail:
            return subprocess.CompletedProcess(command, 1, self.fail[name], "")
        stdout = ""
        if name == "lib":
            stdout = WINDOWS_APP
        elif name == "dumpbin":
            binary = Path(command[-1])
            stdout = f"Dump of file C:\\stage\\{binary.name}\n\n  Image has the following dependencies:\n\n" + \
                "".join(f"    {dll}\n" for dll in self.imports.get(binary.name, []))
        elif name == "makeappx":
            Path(command[-1]).write_bytes(b"PK")
        elif name == "pwsh":
            Path(env["PFX"]).write_bytes(b"pfx")
            Path(env["CER"]).write_bytes(b"cer")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def named(self, name):
        return [command for command in self.commands if command[0] == name]


@pytest.fixture
def stage(tmp_path):
    """A staged app: the manifest beside it, its executable and images in it."""
    stage = tmp_path / "stage"
    (stage / "Assets").mkdir(parents=True)
    (stage / "app.exe").write_bytes(b"MZ")
    for image in IMAGES:
        (stage / "Assets" / image).write_bytes(b"\x89PNG")
    (tmp_path / "Package.appxmanifest").write_text(MANIFEST, encoding="utf-8")
    return stage


def run(stage, *extra, tools=None, env=None):
    """main() over the staged app; its status, the tools stand-in and the package path."""
    tools = tools or Tools()
    out = stage.parent / "out" / "app.msix"
    argv = ["--dir", str(stage), "--manifest", str(stage.parent / "Package.appxmanifest"), "--out", str(out), *extra]
    lib = stage.parent / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "WindowsApp.lib").write_bytes(b"!<arch>")
    status = ci_msix.main(argv, run=tools, env={"LIB": f";{lib}", **(env or {})})
    return status, tools, out


def test_packs_and_signs_with_a_new_self_signed_certificate(stage, capsys):
    status, tools, out = run(stage, "--version", "1.2.3", "--publisher", "CN=Someone & Co")
    assert status == 0
    assert tools.named("makeappx") == [["makeappx", "pack", "/o", "/d", str(stage), "/p", str(out)]]
    (pwsh,) = tools.named("pwsh")
    certificate_env = tools.envs[tools.commands.index(pwsh)]
    assert certificate_env["SUBJECT"] == "CN=Someone & Co" and certificate_env["CER"] == str(out.with_suffix(".cer"))
    (signtool,) = tools.named("signtool")
    assert signtool[:6] == ["signtool", "sign", "/fd", "SHA256", "/f", certificate_env["PFX"]]
    assert signtool[6:] == ["/p", certificate_env["PASSWORD"], str(out)]
    written = (stage / "AppxManifest.xml").read_text(encoding="utf-8")
    assert 'Publisher="CN=Someone &amp; Co" Version="1.2.3.0"' in written
    assert 'IgnorableNamespaces="uap"' in written and "<uap:SplashScreen" in written
    printed = capsys.readouterr().out
    assert f"Package: {out} (1.2.3.0)" in printed and f"Certificate: {out.with_suffix('.cer')}" in printed


def test_without_version_or_publisher_the_manifest_keeps_its_own(stage):
    status, tools, _ = run(stage)
    assert status == 0
    assert 'Publisher="CN=Placeholder" Version="0.0.0.0"' in (stage / "AppxManifest.xml").read_text(encoding="utf-8")
    assert tools.envs[tools.commands.index(tools.named("pwsh")[0])]["SUBJECT"] == "CN=Placeholder"


def test_a_given_certificate_signs_with_its_password(stage, tmp_path, capsys):
    pfx = tmp_path / "signing.pfx"
    status, tools, out = run(stage, "--certificate", str(pfx), env={"MSIX_CERTIFICATE_PASSWORD": "s3cret"})
    assert status == 0 and tools.named("pwsh") == []
    assert tools.named("signtool") == [["signtool", "sign", "/fd", "SHA256", "/f", str(pfx), "/p", "s3cret", str(out)]]
    assert "Certificate:" not in capsys.readouterr().out


@pytest.mark.parametrize(("given", "version"), [("1.2", "1.2.0.0"), ("v1.2.3", "1.2.3.0"), ("01.2.3.65535", "1.2.3.65535")])
def test_versions_become_four_numbers(given, version):
    assert ci_msix.four_part(given) == version


@pytest.mark.parametrize("given", ["1.2.3.4.5", "1.65536", "1.x", ""])
def test_a_version_a_package_cant_have_is_refused(stage, capsys, given):
    with pytest.raises(SystemExit):
        run(stage, "--version", given)
    assert "not a package version" in capsys.readouterr().err


def test_a_manifest_without_identity_fails(stage, capsys):
    (stage.parent / "Package.appxmanifest").write_text("<Package />", encoding="utf-8")
    status, tools, _ = run(stage)
    assert status == 1 and tools.commands == []
    assert "has no <Identity> element" in capsys.readouterr().out


def test_an_identity_without_the_field_to_set_fails(stage, capsys):
    manifest = stage.parent / "Package.appxmanifest"
    manifest.write_text(MANIFEST.replace(' Version="0.0.0.0"', ""), encoding="utf-8")
    status, _, _ = run(stage, "--version", "1.0")
    assert status == 1 and "<Identity> has no Version to set" in capsys.readouterr().out


@pytest.mark.parametrize(("removed", "named"), [
    ("app.exe", "app.exe"),
    ("Assets/StoreLogo.png", "Assets\\StoreLogo.png"),
    ("Assets/Square44x44Logo.scale-200.png", "Assets\\Square44x44Logo.png"),
])
def test_what_the_manifest_names_must_be_staged(stage, capsys, removed, named):
    (stage / removed).unlink()
    status, tools, _ = run(stage)
    assert status == 1 and tools.named("makeappx") == []
    assert f"lacks what the manifest names: {named}" in capsys.readouterr().out


def test_vclibs_copies_the_runtime_dlls_for_the_manifests_architecture(stage, tmp_path, capsys):
    program_files = tmp_path / "pf86"
    appx = program_files / ci_msix.VCLIBS / "14.0" / "Appx" / "Retail" / "x64" / "Microsoft.VCLibs.x64.14.00.appx"
    appx.parent.mkdir(parents=True)
    with zipfile.ZipFile(appx, "w") as package:
        package.writestr("vcruntime140_app.dll", b"MZ1")
        package.writestr("msvcp140_app.dll", b"MZ2")
        package.writestr("AppxManifest.xml", "<Package />")
        package.writestr("nested/other.dll", b"MZ3")
    status, _, _ = run(stage, "--vclibs", env={"ProgramFiles(x86)": str(program_files)})
    assert status == 0
    assert (stage / "vcruntime140_app.dll").read_bytes() == b"MZ1" and (stage / "msvcp140_app.dll").read_bytes() == b"MZ2"
    assert not (stage / "other.dll").exists() and not (stage / "nested").exists()
    assert "VC++ runtime: vcruntime140_app.dll, msvcp140_app.dll" in capsys.readouterr().out


def test_vclibs_without_the_sdk_package_fails(stage, tmp_path, capsys):
    status, _, _ = run(stage, "--vclibs", env={"ProgramFiles(x86)": str(tmp_path / "empty")})
    assert status == 1 and "no Microsoft.VCLibs package for x64" in capsys.readouterr().out


def test_check_imports_fails_on_what_nothing_provides(stage, capsys):
    (stage / "helper.dll").write_bytes(b"MZ")
    tools = Tools(imports={"app.exe": ["helper.dll", "KERNEL32.dll", "d3d11.dll", "MSVCP140.dll"],
                           "helper.dll": ["api-ms-win-core-synch-l1-1-0.dll", "VCRUNTIME140.dll"]})
    status, tools, _ = run(stage, "--check-imports", tools=tools)
    assert status == 1 and tools.named("makeappx") == []
    printed = capsys.readouterr().out
    assert "imports neither packaged nor in WindowsApp.lib:" in printed
    assert "  app.exe: msvcp140.dll\n  helper.dll: vcruntime140.dll" in printed


def test_check_imports_passes_what_the_package_device_or_caller_provides(stage):
    (stage / "helper.dll").write_bytes(b"MZ")
    tools = Tools(imports={"app.exe": ["helper.dll", "kernel32.dll", "Custom.dll"]})
    status, tools, _ = run(stage, "--check-imports", "--allow-import", "custom.DLL", tools=tools)
    assert status == 0 and len(tools.named("dumpbin")) == 2
    assert tools.named("lib")[0][:3] == ["lib", "/nologo", "/list"]


def test_check_imports_needs_windows_app_lib(stage, capsys):
    status = ci_msix.main(["--dir", str(stage), "--manifest", str(stage.parent / "Package.appxmanifest"),
                           "--out", str(stage.parent / "app.msix"), "--check-imports"], run=Tools(), env={})
    assert status == 1 and "no WindowsApp.lib in LIB" in capsys.readouterr().out


def test_a_tool_not_on_path_says_where_it_comes_from(stage, capsys):
    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    status, _, _ = run(stage, tools=missing)
    assert status == 1 and "makeappx isn't on PATH; cpp-build's msvc-uwp puts it there" in capsys.readouterr().out


def test_a_failing_tool_shows_its_output(stage, capsys):
    status, _, _ = run(stage, tools=Tools(fail={"makeappx": "MakeAppx : error: bad manifest\n"}))
    assert status == 1
    assert "makeappx pack failed (1):\nMakeAppx : error: bad manifest" in capsys.readouterr().out


def test_a_publisher_that_isnt_the_certificates_says_so(stage, capsys):
    status, _, _ = run(stage, tools=Tools(fail={"signtool": "SignTool Error: 0x8007000B\n"}))
    assert status == 1 and "The manifest's Publisher must be the certificate's subject." in capsys.readouterr().out


def test_other_signing_failures_are_reported_as_they_are(stage, capsys):
    status, _, _ = run(stage, tools=Tools(fail={"signtool": "SignTool Error: no file\n"}))
    printed = capsys.readouterr().out
    assert status == 1 and "SignTool Error: no file" in printed and "Publisher must be" not in printed


def test_main_reads_the_command_line_and_environment(stage, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ci-msix", "--dir", str(stage), "--manifest",
                                      str(stage.parent / "Package.appxmanifest"), "--out", str(stage.parent / "a.msix")])
    monkeypatch.setenv("CI_MSIX_TEST", "seen")
    tools = Tools()
    assert ci_msix.main(run=tools) == 0
    assert tools.envs[tools.commands.index(tools.named("pwsh")[0])]["CI_MSIX_TEST"] == "seen"
