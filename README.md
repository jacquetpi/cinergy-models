# Cinergy

Cinergy is a framework designed to model the isolated power consumption of virtual resources (such as VMs). By "isolated," we mean that, unlike other energy models, we aim to find the power consumption a resource would have if it were the only one in the system.

This repository contains the model generation component of the framework.

It sets up and measures three different scenarios:
- **Scenario A**: A workload increase using `stress-ng`
- **Scenario B**: A VM operating alone on the server
- **Scenario C**: The same VM activity while colocated with an increasing workload

The objective is to estimate Scenario B's consumption from data obtained in Scenario C, leveraging insights from Scenario A.

## Setup

```bash
apt-get update && apt-get install -y git python3 python3.venv stress-ng
git clone https://github.com/jacquetpi/cinergy-models
cd cinergy-models/
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

The VM setup can be initiated by running the script in ```bash/launchvm.sh```. 
This script should be adapted to configure your VM and generate a workload inside it. 
Our example uses QEMU/KVM with the Libvirt API, with an internal workload based on the Social Network benchmark from DeathStarBench.


## Data generation

The data needed for our models can be generated with a Python tool.
If you prefer to use the data from our experiments, skip this section.

To activate the virtual environment and view the available options:
```bash
source venv/bin/activate
python3 cinergy-model.py --help
```
> RAPL access may require root rights

To start an experiment (add the ```--live``` option for more detailed output):
```bash
source venv/bin/activate
python3 cinergy-model.py host=$(uname -n)
```
> RAPL access may require root rights

This command will run the three scenarios described above and collect measurements. 
The process can be time-consuming (approximately 2 to 12 hours, depending on your setup). 
Multiple CSV files will be generated:
- ```*training-*.csv``` from Scenario A
- ```*groundtruth.csv``` from Scenario B
- ```*cloudlike.csv``` from Scenario C

## Models generation

If you want to load the data from our experiments:
```bash
tar -xvf compressed-data/data.tar.gz
```

Models can be generated from the data using the jupyter notebook in ```notebooks/paper-figures.ipynb```.
