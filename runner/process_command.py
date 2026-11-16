"""Resolve generic argv under the executing business identity, never a shell."""
import os
from pathlib import Path
import shutil


def prepare(command, cwd, environment):
    if not command or not command[0] or any('\x00' in arg for arg in command):
        raise ValueError('business command must contain a valid executable')
    directory=Path(cwd or os.getcwd()).absolute()
    if not directory.is_dir() or not os.access(directory,os.R_OK|os.X_OK):
        raise ValueError('business working directory is missing or inaccessible: '+str(directory))
    executable=command[0]
    if '/' in executable:
        candidate=Path(executable)
        if not candidate.is_absolute():candidate=directory/candidate
    else:
        # Relative PATH entries are relative to business cwd, not the platform runner.
        path=os.pathsep.join(str(directory/p) if not Path(p).is_absolute() else p
                             for p in environment.get('PATH',os.defpath).split(os.pathsep))
        found=shutil.which(executable,path=path)
        if not found:raise ValueError('business executable not found or not executable in PATH: '+executable)
        candidate=Path(found)
    if not candidate.is_file() or not os.access(candidate,os.X_OK):
        raise ValueError('business executable is missing or not executable: '+str(candidate))
    return [str(candidate),*command[1:]]


def for_template(template, command, cwd, environment):
    settings=template.get('host_runtime') or template.get('runtime') or {}
    return prepare(command,cwd,environment) if settings.get('command_mode')=='exec' else command
