"""``python -m midiseq`` — the same thing as the ``seq`` command.

Worth having: on Windows, ``pip install -e .`` puts ``seq.exe`` in a Scripts
directory that isn't always on PATH, and "the command doesn't exist" is a bad
first experience of a tool that's working fine.
"""

from .cli.seq import main

if __name__ == "__main__":
    raise SystemExit(main())
