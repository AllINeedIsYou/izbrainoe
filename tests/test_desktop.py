import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('desktop_launcher', Path(__file__).resolve().parents[1] / 'scripts/desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


@pytest.mark.parametrize('command', ['python /another/run.py', f'python {desktop.ROOT}/run.py.unrelated', ''])
def test_stop_never_signals_unrelated_or_stale_pid(tmp_path, monkeypatch, command):
    (tmp_path / 'server.pid').write_text('123')
    monkeypatch.setattr(desktop.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=command))
    def unexpected(*a):
        pytest.fail('Unrelated process must not receive a signal')
    monkeypatch.setattr(desktop.os, 'kill', unexpected)
    assert desktop.stop(SimpleNamespace(data_dir=tmp_path))['running'] is False


def test_repeated_launch_reuses_running_server(tmp_path, monkeypatch):
    state = {'running': True, 'bot_running': True, 'url': 'http://127.0.0.1/local?key=test_only'}
    monkeypatch.setattr(desktop, 'local_state', lambda config: state)
    def unexpected(*a, **kw):
        pytest.fail('Repeated launch must not spawn another server')
    monkeypatch.setattr(desktop.subprocess, 'Popen', unexpected)
    assert desktop.start(SimpleNamespace(data_dir=tmp_path)) == state


def test_cold_launch_detaches_server_and_waits_for_telegram(tmp_path, monkeypatch):
    starting = {'running': True, 'bot_running': False, 'bot_configured': True, 'bot_error': False}
    ready = {**starting, 'bot_running': True}
    states = iter([None, starting, ready])
    monkeypatch.setattr(desktop, 'local_state', lambda config: next(states))
    monkeypatch.setattr(desktop, 'server_pid', lambda folder: None)
    monkeypatch.setattr(desktop.time, 'sleep', lambda delay: None)
    calls = []
    monkeypatch.setattr(desktop.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    assert desktop.start(SimpleNamespace(data_dir=tmp_path)) == ready
    assert len(calls) == 1
    args, options = calls[0]
    assert args[0] == [str(desktop.ROOT / '.venv/bin/python'), str(desktop.ROOT / 'run.py')]
    assert options['start_new_session'] is True
    assert options['env']['IZ_NO_BROWSER'] == '1'


def test_launch_during_startup_does_not_spawn_second_server(tmp_path, monkeypatch):
    ready = {'running': True, 'bot_running': True, 'bot_configured': True, 'bot_error': False}
    states = iter([None, ready])
    monkeypatch.setattr(desktop, 'local_state', lambda config: next(states))
    monkeypatch.setattr(desktop, 'server_pid', lambda folder: 123)
    def unexpected(*a, **kw):
        pytest.fail('An existing server is already starting')
    monkeypatch.setattr(desktop.subprocess, 'Popen', unexpected)
    assert desktop.start(SimpleNamespace(data_dir=tmp_path)) == ready


def test_corrupt_pid_file_is_safe(tmp_path):
    (tmp_path / 'server.pid').write_text('-1')
    assert desktop.server_pid(tmp_path) is None
    (tmp_path / 'server.pid').write_text('invalid')
    assert desktop.server_pid(tmp_path) is None


def test_chat_connection_does_not_mean_phone_app_is_ready(tmp_path,monkeypatch):
    connecting={'running':True,'bot_running':True,'bot_configured':True,'bot_error':False,
                'phone_required':True,'phone_ready':False}
    ready={**connecting,'phone_ready':True}
    states=iter([connecting,connecting,ready])
    monkeypatch.setattr(desktop,'local_state',lambda config:next(states))
    monkeypatch.setattr(desktop.time,'sleep',lambda delay:None)
    assert not desktop.fully_ready(connecting)
    assert desktop.start(SimpleNamespace(data_dir=tmp_path))==ready
