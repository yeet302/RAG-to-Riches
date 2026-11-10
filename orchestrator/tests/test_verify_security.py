"""security/verify_security.py flags keys left in config files once Key Vault is on."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_security", ROOT / "security" / "verify_security.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def run(tmp_path, capsys, files):
    paths = {}
    for service, text in files.items():
        paths[service] = tmp_path / f"{service}.env"
        paths[service].write_text(text)
    report = verify.Report()
    verify.check_config(report, paths)
    return report.failed, capsys.readouterr().out


def test_key_left_in_a_file_with_key_vault_on_fails(tmp_path, capsys):
    failed, out = run(tmp_path, capsys, {"code": "KEY_VAULT_URL=https://kv.vault.azure.net/\nGITHUB_TOKEN=abc\n"})
    assert failed == 1 and "GITHUB_TOKEN still in the file" in out


def test_key_vault_on_and_no_keys_passes(tmp_path, capsys):
    failed, out = run(tmp_path, capsys, {
        "docs": "KEY_VAULT_URL=https://kv.vault.azure.net/\nAZURE_AI_API_KEY=\nREQUIRE_AGENT_AUTH=true\n"})
    assert failed == 0 and "Key Vault on, no keys in the file" in out and "REQUIRE_AGENT_AUTH=true" in out


def test_without_key_vault_keys_are_a_warning_not_a_failure(tmp_path, capsys):
    failed, out = run(tmp_path, capsys, {"logs": "LOGS_AGENT_TOKEN=abc\n"})
    assert failed == 0 and "WARN" in out and "keys in file: LOGS_AGENT_TOKEN" in out


def test_env_parser_handles_quotes_comments_and_export(tmp_path):
    path = tmp_path / "x.env"
    path.write_text("# comment\nexport A='1'\nB=\"two\"\n\nC=3=3\n")
    assert verify.read_env(path) == {"A": "1", "B": "two", "C": "3=3"}
