"""Exercise installer branches in Bash without installing packages or touching services."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'install.sh').read_text()
PRELUDE = SOURCE.split('# ── Parse args')[0]
BASH = '/bin/bash'


def function(name):
    lines = SOURCE.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(name + '() {'))
    if lines[start].rstrip().endswith('}'):
        return lines[start]
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip('\n') == '}')
    return ''.join(lines[start:end + 1])


def run(tmp_path, body, *args, functions=(), env=None):
    script = PRELUDE + '\n' + '\n'.join(function(n) for n in functions) + '\n' + body
    return subprocess.run([BASH, '-c', script, 'installer-test', *args], capture_output=True,
                          text=True, timeout=15,
                          env={**os.environ, 'HUSHCLAW_HOME':str(tmp_path), 'NO_COLOR':'1', **(env or {})})


@pytest.mark.parametrize('args', [[], ['--update'], ['--distro', 'personal', 'path with spaces', '*', 'quoted"value']])
def test_reexec_preserves_empty_and_nonempty_arguments(tmp_path, args):
    target = tmp_path / 'updated installer.sh'
    target.write_text('set -eu\nprintf "argc=%s\\n" "$#"\nfor arg in "$@"; do printf "[%s]\\n" "$arg"; done\n')
    result = run(tmp_path, '_REPO_INSTALLER="$TARGET"; restart_installer', *args, env={'TARGET':str(target)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f'argc={len(args)}', *[f'[{a}]' for a in args]]


@pytest.mark.parametrize('command,expected', [
    ('/venv/bin/python /venv/bin/hushclaw serve --port 8765', True),
    ('python -m hushclaw serve --port 8765', True),
    ('python -m http.server 8765', False),
    ('/bin/zsh -c "hushclaw serve"', False),
    ('python /tmp/not-hushclaw serve', False),
])
def test_process_identity_not_just_port_or_pid(tmp_path, command, expected):
    result = run(tmp_path, '''
ps() { printf '%s\n' "$MOCK_COMMAND"; }
is_hushclaw_pid 123456
''', functions=['is_hushclaw_pid'], env={'MOCK_COMMAND':command})
    assert (result.returncode == 0) == expected


def test_foreign_port_is_not_stopped_or_reported_as_hushclaw(tmp_path):
    (tmp_path/'hushclaw.pid').write_text('123456')
    result = run(tmp_path, '''
ps() { echo 'python -m http.server 8765'; }
port_listener_pids() { echo 123456; }
kill() { echo 'UNSAFE KILL'; }
[[ -z "$(find_running_pid)" ]]
check_port_owner
''', functions=['is_hushclaw_pid','find_running_pid','check_port_owner'])
    assert result.returncode != 0
    assert 'used by another application' in result.stderr
    assert 'UNSAFE KILL' not in result.stdout


def test_homebrew_download_failure_never_claims_success(tmp_path):
    result = run(tmp_path, '''
activate_homebrew() { return 1; }
curl() { return 22; }
ensure_homebrew
''', functions=['has_install_terminal','ensure_homebrew'])
    assert result.returncode != 0
    assert 'Could not download' in result.stderr
    assert 'Homebrew is ready' not in result.stdout


def test_homebrew_noninteractive_install_verifies_activation(tmp_path):
    installer = tmp_path/'mock-brew.sh'
    installer.write_text('printf "mode=%s\\n" "${NONINTERACTIVE:-interactive}"; touch "$BREW_READY"\n')
    result = run(tmp_path, '''
activate_homebrew() { [[ -f "$BREW_READY" ]]; }
has_install_terminal() { return 1; }
curl() { cp "$MOCK_INSTALLER" "${@: -1}"; }
ensure_homebrew
''', functions=['ensure_homebrew'], env={'MOCK_INSTALLER':str(installer),'BREW_READY':str(tmp_path/'ready')})
    assert result.returncode == 0, result.stderr
    assert 'mode=1' in result.stdout
    assert 'Homebrew is ready' in result.stdout


def test_existing_homebrew_is_reused_without_download(tmp_path):
    result = run(tmp_path, '''
activate_homebrew() { return 0; }
curl() { echo UNEXPECTED_DOWNLOAD; return 1; }
ensure_homebrew
''', functions=['ensure_homebrew'])
    assert result.returncode == 0
    assert 'UNEXPECTED_DOWNLOAD' not in result.stdout


@pytest.mark.parametrize('exists', [True, False])
def test_missing_python_runs_homebrew_then_python_install(tmp_path, exists):
    step = SOURCE.split('# ── Step 1: Python')[1].split('# ── Step 2: Git')[0]
    step = step[step.index('\n'):]
    result = run(tmp_path, '''
OS_NAME=macOS
ready="$PYTHON_EXISTS"
find_python() { if [[ "$ready" == 1 ]]; then PYTHON=/fake/python; return 0; fi; return 1; }
ensure_homebrew() { echo BREW; }
install_python_macos() { echo PYTHON_INSTALL; ready=1; }
''' + step, env={'PYTHON_EXISTS':str(int(exists))})
    assert result.returncode == 0, result.stderr
    assert ('BREW' in result.stdout) != exists
    assert ('PYTHON_INSTALL' in result.stdout) != exists


def test_failed_upgrade_attempts_service_recovery_and_stays_failed(tmp_path):
    result = run(tmp_path, '''
SERVICE_RESTORE_KIND=launchd
launchctl() { printf 'launchctl %s\n' "$*"; }
wait_for_server() { return 0; }
trap restore_service_on_error EXIT
exit 17
''', functions=['restore_service_on_error'])
    assert result.returncode == 17
    assert 'launchctl load' in result.stdout
    assert 'reachable again' in result.stdout
    assert 'update itself did not complete' in result.stdout


def test_failed_fresh_install_does_not_start_a_service(tmp_path):
    result = run(tmp_path, '''
SERVICE_RESTORE_KIND=""
launchctl() { echo UNEXPECTED_SERVICE; }
trap restore_service_on_error EXIT
exit 9
''', functions=['restore_service_on_error'])
    assert result.returncode == 9
    assert 'UNEXPECTED_SERVICE' not in result.stdout


@pytest.mark.parametrize('page,healthy', [('<div id="panel-chat">HushClaw</div>', True), ('<h1>Directory listing</h1>', False)])
def test_readiness_requires_hushclaw_page(tmp_path, page, healthy):
    result = run(tmp_path, '''
curl() { printf '%s' "$MOCK_PAGE" > "${@: -1}"; }
sleep() { :; }
wait_for_server 1
''', functions=['wait_for_server'], env={'MOCK_PAGE':page})
    assert (result.returncode == 0) == healthy


def test_task_output_is_quiet_but_failure_keeps_exit_code_and_log(tmp_path):
    result = run(tmp_path, '''
INSTALL_LOG="$INSTALL_DIR/setup.log"
section "Environment"
run_step "Dependency install" bash -c 'echo diagnostic-detail; exit 23'
''')
    assert result.returncode == 23
    assert '01  Environment' in result.stdout
    assert 'failed (exit 23)' in result.stderr
    assert 'diagnostic-detail' in (tmp_path/'setup.log').read_text()
    assert '\x1b' not in result.stdout + result.stderr


def test_fetch_precedes_stop_and_startup_precedes_success_summary():
    update = SOURCE[SOURCE.index('      run_step "Fetch latest release"'):]
    assert update.index('fetch --quiet') < update.index('stop_for_install') < update.index('git reset --hard')
    start = SOURCE[SOURCE.index('  check_port_owner\n  start_background'):]
    assert start.index('wait_for_server') < start.index('show_install_summary')


def test_shell_syntax_and_help_have_no_side_effects(tmp_path):
    assert subprocess.run([BASH, '-n', str(ROOT/'install.sh')]).returncode == 0
    result = subprocess.run([BASH,str(ROOT/'install.sh'),'--help'],capture_output=True,text=True,
                            env={**os.environ,'HUSHCLAW_HOME':str(tmp_path/'not-created')})
    assert result.returncode == 0
    assert not (tmp_path/'not-created').exists()


def test_homebrew_keeps_interactive_terminal_for_authorization(tmp_path):
    import pty
    import select
    import signal
    import time

    installer = tmp_path/'interactive-brew.sh'
    installer.write_text('[[ -t 0 ]] || exit 41\n[[ -z "${NONINTERACTIVE:-}" ]] || exit 42\necho TERMINAL_AVAILABLE\ntouch "$BREW_READY"\n')
    script = PRELUDE + function('has_install_terminal') + function('ensure_homebrew') + '''
activate_homebrew() { [[ -f "$BREW_READY" ]]; }
curl() { cp "$MOCK_INSTALLER" "${@: -1}"; }
ensure_homebrew
'''
    env={**os.environ,'HUSHCLAW_HOME':str(tmp_path),'MOCK_INSTALLER':str(installer),
         'BREW_READY':str(tmp_path/'ready'),'NO_COLOR':'1'}
    env.pop('NONINTERACTIVE',None)
    pid, master=pty.fork()
    if pid==0:
        os.execve(BASH,[BASH,'-c',script],env)
    output=bytearray()
    status=None
    ended=0
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            readable,_,_=select.select([master],[],[],0.1)
            if readable:
                try:
                    chunk=os.read(master,65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            ended,status=os.waitpid(pid,os.WNOHANG)
            if ended:
                break
        else:
            os.kill(pid,signal.SIGKILL)
            pytest.fail('Mock Homebrew hung waiting for a terminal')
        if status is None or not ended:
            _,status=os.waitpid(pid,0)
    finally:
        os.close(master)
    assert os.waitstatus_to_exitcode(status)==0,output.decode(errors='replace')
    assert b'TERMINAL_AVAILABLE' in output


def test_reexec_failure_keeps_service_recovery_context(tmp_path):
    child = tmp_path/'updated.sh'
    child.write_text(PRELUDE + function('restore_service_on_error') + '''
launchctl() { echo RECOVERY_REQUESTED; }
wait_for_server() { return 0; }
trap restore_service_on_error EXIT
exit 19
''')
    result = run(tmp_path, '''
export HUSHCLAW_INSTALL_RESTORE_KIND=launchd
_REPO_INSTALLER="$TARGET"
restart_installer
''', env={'TARGET':str(child)})
    assert result.returncode==19
    assert 'RECOVERY_REQUESTED' in result.stdout
    assert 'update itself did not complete' in result.stdout
