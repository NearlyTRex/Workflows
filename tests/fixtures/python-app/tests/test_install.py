import subprocess


def test_install_needs_a_port():
    result = subprocess.run(["bash", "scripts/install.sh"], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_install_writes_the_config_and_enables_the_service(shell_sandbox):
    result = shell_sandbox.run("scripts/install.sh", "8080")
    assert result.returncode == 0, result.stderr
    assert shell_sandbox.read("/etc/fixture-app/config") == "port=8080\n"
    assert shell_sandbox.ran("systemctl", "enable", "fixture-app")
