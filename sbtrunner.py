import sublime

try:
    from .project import Project
    from .util import OnePerWindow
except(ValueError):
    from project import Project
    from util import OnePerWindow

import os
import pipes
import signal
import subprocess
import threading


class SbtRunner(OnePerWindow):

    @classmethod
    def is_sbt_running_for(cls, window):
        return cls(window).is_sbt_running()

    def __init__(self, window):
        self._project = Project(window)
        self._proc = None

    def project_root(self):
        return self._project.project_root()

    def sbt_command(self, command):
        cmdline = self._project.sbt_command()
        if command is not None:
            cmdline.append(command)
        return cmdline

    def start_sbt(self, command, on_start, on_stop, on_stdout, on_stderr):
        if self.project_root() and not self.is_sbt_running():
            self._proc = self._try_start_sbt_proc(self.sbt_command(command),
                                                  on_start,
                                                  on_stop,
                                                  on_stdout,
                                                  on_stderr)

    def stop_sbt(self):
        if self.is_sbt_running():
            self._proc.terminate()

    def kill_sbt(self):
        if self.is_sbt_running():
            self._proc.kill()

    def is_sbt_running(self):
        return (self._proc is not None) and self._proc.is_running()

    def send_to_sbt(self, input):
        if self.is_sbt_running():
            self._proc.send(input)

    def _try_start_sbt_proc(self, cmdline, *handlers):
        try:
            return SbtProcess.start(cmdline, self.project_root(), *handlers)
        except OSError:
            msg = ('Unable to find "%s".\n\n'
                   'You may need to specify the full path to your sbt command.'
                   % cmdline[0])
            sublime.error_message(msg)


class SbtProcess(object):

    @staticmethod
    def start(cmdline, cwd, *handlers):
        if sublime.platform() == 'windows':
            return SbtWindowsProcess._start(cmdline, cwd, *handlers)
        else:
            return SbtUnixProcess._start(cmdline, cwd, *handlers)

    @classmethod
    def _start(cls, cmdline, cwd, *handlers):
        return cls(cls._start_proc(cmdline, cwd), *handlers)

    @classmethod
    def _start_proc(cls, cmdline, cwd):
        return cls._popen(cmdline,
                          stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          cwd=cwd)

    def __init__(self, proc, on_start, on_stop, on_stdout, on_stderr):
        self._proc = proc
        on_start()
        if self._proc.stdout:
            self._start_thread(self._monitor_output,
                               (self._proc.stdout, on_stdout))
        if self._proc.stderr:
            self._start_thread(self._monitor_output,
                               (self._proc.stderr, on_stderr))
        self._start_thread(self._monitor_proc, (on_stop,))

    def is_running(self):
        return self._proc.returncode is None

    def send(self, input):
        self._proc.stdin.write(input.encode())
        self._proc.stdin.flush()

    def _monitor_output(self, pipe, handle_output):
        while True:
            output = os.read(pipe.fileno(), 2 ** 15).decode()
            if output != "":
                handle_output(output)
            else:
                pipe.close()
                return

    def _monitor_proc(self, handle_stop):
        self._proc.wait()
        sublime.set_timeout(handle_stop, 0)

    def _start_thread(self, target, args):
        threading.Thread(target=target, args=args).start()


class SbtUnixProcess(SbtProcess):

    @classmethod
    def _popen(cls, cmdline, **kwargs):
        return subprocess.Popen(cls._shell_cmdline(cmdline),
                                preexec_fn=os.setpgrp,
                                **kwargs)

    @classmethod
    def _shell_cmdline(cls, cmdline):
        shell = os.environ.get('SHELL', '/bin/bash')
        opts = '-ic' if shell.endswith('csh') else '-lic'
        cmd = ' '.join(map(pipes.quote, cmdline))
        return [shell, opts, cmd]

    def terminate(self):
        os.killpg(self._proc.pid, signal.SIGTERM)

    def kill(self):
        os.killpg(self._proc.pid, signal.SIGKILL)


class SbtWindowsProcess(SbtProcess):

    SBT_OPTS = '-Djline.terminal=jline.UnsupportedTerminal'

    @classmethod
    def _popen(cls, cmdline, **kwargs):
        return subprocess.Popen(cmdline,
                                shell=True,
                                env=cls._sbt_env(),
                                **kwargs)

    @classmethod
    def _sbt_env(cls):
        return dict(list(os.environ.items()) + [['SBT_OPTS', cls._sbt_opts()]])

    @classmethod
    def _sbt_opts(cls):
        existing_opts = os.environ.get('SBT_OPTS', None)
        if existing_opts is None:
            return cls.SBT_OPTS
        else:
            return existing_opts + ' ' + cls.SBT_OPTS

    def terminate(self):
        self.kill()

    def kill(self):
        cmdline = ['taskkill', '/T', '/F', '/PID', str(self._proc.pid)]
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        subprocess.call(cmdline, startupinfo=si)
