Cinergy is a framework aiming to model the isolated power consumption of virtual resources (such as VMs).
By isolated, we mean that, unlike others energy models, we want to find the power consumption a resource would have if it was alone in the system

This is the models generation part of the framework.

## Setup

```bash
apt-get update && apt-get install -y git python3 python3.venv stress-ng
git clone https://github.com/jacquetpi/slackvm
cd cinergy-models/
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Features

Generate energy models

## Usage

```bash
source venv/bin/activate
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
