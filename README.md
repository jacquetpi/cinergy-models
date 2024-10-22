# Model generators for cinergy

Measure and calibrate an energy model

```bash
apt install stress-ng
```

## Features

Generate energy models

## Usage

```bash
python3 cinergy-model.py --help
```

/!\ RAPL access may require root rights

To dump on default ```consumption.csv``` while also displaying measures to the console
```bash
python3 cinergy-model.py --live
```

To change default values:
```bash
python3 cinergy-model.py --delay=(sec) --precision=(number of digits) --output=prefix
```
