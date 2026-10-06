from .cli import main

# guarded: merge workers are spawned processes that re-import __main__
if __name__ == "__main__":
    raise SystemExit(main())
