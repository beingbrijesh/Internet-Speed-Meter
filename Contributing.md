# Contributing

Thanks for your interest in Internet Speed Meter.

## Reporting a bug

Open an issue and include:

- macOS version and chip (for example, macOS 26, Apple M2)
- Python version, or whether you used the `.app`
- What you expected and what happened
- Any terminal output (run `python3 speed_meter.py` in the foreground to see errors)

## Suggesting a feature

Open an issue describing the use case. Keep in mind the goals: a single file, low memory, and the Liquid Glass look.

## Pull requests

1. Fork the repo and create a branch
2. Keep changes small and focused. Do not reformat unrelated code.
3. Do not change the visual design unless the PR is specifically about it
4. Test on a real Mac: menu bar item, panel open and close, all three styles, and Run Full Test
5. If you touch drawing code, check memory over time:

```sh
footprint -p "$(pgrep -f 'Python.*speed_meter.py' | head -1)" | grep Footprint:
```

The footprint should stay flat while idle (about 37 MB on Apple Silicon with Python 3.11).

6. Update `CHANGELOG.md`

## Local setup

```sh
git clone https://github.com/beingbrijesh/Internet-Speed-Meter.git
cd Internet-Speed-Meter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 speed_meter.py
```