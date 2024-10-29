import sys, getopt, re, time
from os import listdir, kill, killpg, getpgid, setsid, remove
from os.path import isfile, join, exists
import subprocess, signal

OUTPUT_PREFIX   = 'consumption'
OUTPUT_HEADER = 'timestamp,domain,measure'
OUTPUT_NL     = '\n'
ROOT_FS       ='/sys/class/powercap/'
PRECISION     = 2
SYSFS_STAT    = '/proc/stat'
SYSFS_TOPO    = '/sys/devices/system/cpu/'
SYSFS_FREQ    = '/sys/devices/system/cpu/{core}/cpufreq/scaling_cur_freq'
# From https://www.kernel.org/doc/Documentation/filesystems/proc.txt
SYSFS_STATS_KEYS  = {'cpuid':0, 'user':1, 'nice':2 , 'system':3, 'idle':4, 'iowait':5, 'irq':6, 'softirq':7, 'steal':8, 'guest':9, 'guest_nice':10}
SYSFS_STATS_IDLE  = ['idle', 'iowait']
SYSFS_STATS_NTID  = ['user', 'nice', 'system', 'irq', 'softirq', 'steal']
LIVE_DISPLAY = False
PER_CACHE_USAGE = None
VM_CONNECTOR    = None
MODEL_MEASURE_WINDOW = 2
MODEL_ITERATION = 10
MODEL_STEP = [25, 50, 100] # Percentage of load per step

def print_usage():
    print('python3 rapl-reader.py [--help] [--live] [--explicit] [--vm=qemu:///system] [--output=' + OUTPUT_PREFIX + '] [--precision=' + str(PRECISION) + ' (number of decimal)]')

###########################################
# Find relevant sysfs
###########################################
def find_rapl_sysfs():
    regex = '^intel-rapl:[0-9]+.*$'
    folders = [f for f in listdir(ROOT_FS) if re.match(regex, f)]
    # package0: cpu, cores: cores of cpu, uncore : gpu, psys: platform ...
    sysfs = dict()
    for folder in folders:
        base = ROOT_FS + folder
        with open(base + '/name') as f:
            domain = f.read().replace('\n','')
        if '-' not in domain: domain+= '-' + folder.split(':')[1] # We guarantee name unicity
        sysfs[domain] = base + '/energy_uj'
    return sysfs

def find_cpuid_per_numa():
    regex = '^cpu[0-9]+$'
    cpu_found = [int(re.sub("[^0-9]", '', f)) for f in listdir(SYSFS_TOPO) if not isfile(join('topology', f)) and re.match(regex, f)]
    cpu_per_numa = dict()
    for cpu in cpu_found:
        path = SYSFS_TOPO + 'cpu' + str(cpu) + '/topology/physical_package_id'
        if not exists(path): continue
        with open(path, 'r') as f:
            numa_id = int(f.read())
        if numa_id not in cpu_per_numa: cpu_per_numa[numa_id] = list()
        cpu_per_numa[numa_id].append('cpu' + str(cpu))
    return cpu_per_numa

def find_cache_topo():
    regex_cpu = '^cpu[0-9]+$'
    regex_idx = '^index[0-9]+$'
    cpu_found = [int(re.sub("[^0-9]", '', f)) for f in listdir(SYSFS_TOPO) if re.match(regex_cpu, f)]
    cpu_per_cache = dict()
    cpu_found.sort()
    for cpu in cpu_found:
        path = SYSFS_TOPO + 'cpu' + str(cpu) + '/cache'
        cache_found = [int(re.sub("[^0-9]", '', f)) for f in listdir(path) if not isfile(f) and re.match(regex_idx, f)]
        cache_found.sort()
        prev_shared = None
        cache_list_for_cpu = dict()
        for cache_index in cache_found:
            path_completed = path + '/' + 'index' + str(cache_index)
            with open(path_completed + '/shared_cpu_list', 'r') as f:
                shared = f.read()
                if shared == prev_shared:
                    continue
            with open(path_completed + '/id', 'r') as f:
                cache_id = int(f.read())
            cache_list_for_cpu[cache_index] = cache_id
            prev_shared = shared

        cache_index_of_interest = list(cache_list_for_cpu.keys())
        cache_index_of_interest.sort(reverse=True)
        prev_elem = cpu_per_cache

        for cache_index_unique in cache_index_of_interest:
            key = 'L' + str(cache_index_unique) + '-' + str(cache_list_for_cpu[cache_index_unique])
            if cache_index_unique != min(cache_index_of_interest):
                if key not in prev_elem:
                    prev_elem[key] = dict()
                prev_elem = prev_elem[key]
            else:
                if key not in prev_elem:
                    prev_elem[key] = list()
                prev_elem[key].append(cpu)
                break

    return cpu_per_cache

###########################################
# Read CPU usage
###########################################
def read_cpu_usage(cpuid_per_numa : dict, hist :dict):
    measures = dict()
    global_usage = get_usage_global(cputime_hist=hist)
    if global_usage != None: measures['cpu%_package-global'] = round(global_usage, PRECISION)
    for numa_id, cpuid_list in cpuid_per_numa.items():
        numa_usage = get_usage_of(server_cpu_list=cpuid_list, cputime_hist=hist)
        numa_freq  = get_freq_of(server_cpu_list=cpuid_list)
        if numa_usage != None: 
            measures['cpu%_package-' + str(numa_id)] = numa_usage
            measures['freq_package-' + str(numa_id)] = numa_freq
    return measures

class CpuTime(object):
    def has_time(self):
        return hasattr(self, 'idle') and hasattr(self, 'not_idle')

    def set_time(self, idle : int, not_idle : int):
        setattr(self, 'idle', idle)
        setattr(self, 'not_idle', not_idle)

    def get_time(self):
        return getattr(self, 'idle'), getattr(self, 'not_idle')

    def clear_time(self):
        if hasattr(self, 'idle'): delattr(self, 'idle')
        if hasattr(self, 'not_idle'): delattr(self, 'not_idle')

def get_usage_global(cputime_hist : dict):
    with open(SYSFS_STAT, 'r') as f:
        split = f.readlines()[0].split(' ')
        split.remove('')
    if 'global' not in cputime_hist: cputime_hist['global'] = CpuTime()
    return __get_usage_of_line(split=split, hist_object=cputime_hist['global'])

def get_usage_of(server_cpu_list : list, cputime_hist : dict):
    cumulated_cpu_usage = 0
    with open(SYSFS_STAT, 'r') as f:
        lines = f.readlines()

    for line in lines:
        split = line.split(' ')
        if not split[SYSFS_STATS_KEYS['cpuid']].startswith('cpu'): break
        if split[SYSFS_STATS_KEYS['cpuid']] not in server_cpu_list: continue

        if split[SYSFS_STATS_KEYS['cpuid']] not in cputime_hist: cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]] = CpuTime()
        hist_object = cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]]

        cpu_usage = __get_usage_of_line(split=split, hist_object=hist_object)

        # Add usage to cumulated value
        if cumulated_cpu_usage != None and cpu_usage != None:
            cumulated_cpu_usage+=cpu_usage
        else: cumulated_cpu_usage = None # Do not break to compute others initializing values

    if cumulated_cpu_usage != None: 
        cumulated_cpu_usage = round(cumulated_cpu_usage/len(server_cpu_list), PRECISION)
    return cumulated_cpu_usage

def __get_usage_of_line(split : list, hist_object : object, update_history : bool = True):
    idle          = sum([ int(split[SYSFS_STATS_KEYS[idle_key]])     for idle_key     in SYSFS_STATS_IDLE])
    not_idle      = sum([ int(split[SYSFS_STATS_KEYS[not_idle_key]]) for not_idle_key in SYSFS_STATS_NTID])

    # Compute delta
    cpu_usage  = None
    if hist_object.has_time():
        prev_idle, prev_not_idle = hist_object.get_time()
        delta_idle     = idle - prev_idle
        delta_total    = (idle + not_idle) - (prev_idle + prev_not_idle)
        if delta_total>0: # Manage overflow
            cpu_usage = ((delta_total-delta_idle)/delta_total)*100

    if update_history: hist_object.set_time(idle=idle, not_idle=not_idle)
    return cpu_usage

def read_core_usage(cputime_hist : dict, update_history : bool):
    with open(SYSFS_STAT, 'r') as f:
        lines = f.readlines()

    measures = dict()
    lines.pop(0) # remove global line, we focus on per cpu usage
    for line in lines:
        split = line.split(' ')
        if not split[SYSFS_STATS_KEYS['cpuid']].startswith('cpu'): break

        if split[SYSFS_STATS_KEYS['cpuid']] not in cputime_hist: cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]] = CpuTime()
        cpu_usage = __get_usage_of_line(split=split, hist_object=cputime_hist[split[SYSFS_STATS_KEYS['cpuid']]], update_history=update_history)
        measures['cpu%_' + split[SYSFS_STATS_KEYS['cpuid']]] = cpu_usage

    return measures

def display_cache_usage(cache_topo : dict, cputime_hist : dict):
    core_usage = read_core_usage(cputime_hist=cputime_hist, update_history=False)
    associate_usage_to_cache_levels(core_usage=core_usage, cache_topo=cache_topo)
    print('###')

def associate_usage_to_cache_levels(core_usage : dict, cache_topo, label = None, padding_length = -2):
    if isinstance(cache_topo, dict):
        cpu_list = list()
        for cache_id in cache_topo.keys():

            child_label = ""
            if label is not None: child_label = label + '_'
            child_label += cache_id

            cpu_list.extend(associate_usage_to_cache_levels(core_usage=core_usage, cache_topo=cache_topo[cache_id], label=child_label, padding_length=padding_length+2))

        if label is not None:
            values = [core_usage['cpu%_cpu' + str(cpu)] for cpu in cpu_list]
            if None not in values:
                usage = sum(values) / len(cpu_list)
                line = ' ' * padding_length
                line += label + ' : ' + str(usage)
                print(line)
        return cpu_list

    else:
        if label is not None:
            values = [core_usage['cpu%_cpu' + str(cpu)] for cpu in cache_topo]
            if None not in values:
                usage = sum(values) / len(cache_topo)
                line = ' ' * padding_length
                line += label + ' : ' + str(usage)
                if usage>51: print(line)
        return cache_topo

def get_freq_of(server_cpu_list : list):
    cumulated_cpu_freq = 0
    for cpu in server_cpu_list:
        with open(SYSFS_FREQ.replace('{core}', str(cpu)), 'r') as f:
            cumulated_cpu_freq+= int(f.read())
    return round(cumulated_cpu_freq/len(server_cpu_list), PRECISION)

###########################################
# Find and Read specific process
###########################################

def find_process(keyword : str = 'qemu'):
    """Use pgrep to find the first PID matching a name process."""
    try:
        result = subprocess.run(['pgrep', 'qemu'], capture_output=True, text=True)
        if result.stdout:
            return int(result.stdout.strip().split()[0])  # Return the first QEMU PID found
        else:
            return None
    except subprocess.SubprocessError as e:
        return None

def get_child_process(pid):
    """Return a list of child PIDs of the given process from /proc/<pid>/task/<pid>/children."""
    try:
        result = subprocess.run(['ls', '/proc/' + str(pid) + '/task'], capture_output=True, text=True)
        if result.stdout:
            children = [int(child) for child in result.stdout.strip().split()]
            children.remove(pid)
            return children
    except FileNotFoundError:
       pass
    return []

def does_file_exist(file : str, to_be_removed : bool = False):
    try:
        with open(file, 'r') as f:
            pass
        if to_be_removed: remove(file)
        return True
    except FileNotFoundError:
        return False

def get_pid_name(pid):
    try:
        with open(f'/proc/{pid}/stat', 'r') as f:
            stat_line = f.read()
            comm = stat_line[stat_line.find("(")+1:stat_line.find(")")]
            return comm.replace(' ','')
    except FileNotFoundError:
        return None

def read_process_stat(pid):
    """Read the CPU usage times (utime, stime) from /proc/<pid>/stat."""
    try:
        with open(f'/proc/{pid}/stat', 'r') as f:
            stat_line = f.read()
            comm = stat_line[stat_line.find("(")+1:stat_line.find(")")]
            # comm field may contains a whitespace that we need to treat
            # comm name is CPUx if it is a QEMU/KVM pid
            stat_info = stat_line.replace(comm, comm.replace(' ','')).split()
            # utime is the 14th field, stime is the 15th field
            utime = int(stat_info[13])
            stime = int(stat_info[14])
            return (utime + stime) * (10**7) # jiffies (10^-2) to ns
    except FileNotFoundError:
        return None

def read_process_schedstat(pid):
    """Read the CPU usage times (utime, stime) from /proc/<pid>/stat."""
    try:
        with open(f'/proc/{pid}/schedstat', 'r') as f:
            schedstat_line = f.read().split()
            cputime = int(schedstat_line[0]) # time spent on the cpu (in nanoseconds)
            return cputime # ns
    except FileNotFoundError:
        return None

process_hist_dict = {}
def get_process_usage(pid : int, curr_time : int): # curr_time in ns
    """Calculate the CPU usage percentage of a given pid since last call."""
    global process_hist_dict
    usage = None
    if (curr_time is not None):
        curr_timestamp = time.time_ns() # 10^-9
        if str(pid) in process_hist_dict:
            prev_timestamp, prev_time = process_hist_dict[str(pid)]
            elapsed_time = (curr_timestamp - prev_timestamp)
            usage = round(((curr_time - prev_time) / elapsed_time)*100,PRECISION)
        process_hist_dict[str(pid)] = (curr_timestamp, curr_time)
    return usage

def monitor_process(process_as_dict : dict, replace_pid_per_label : dict = None, output_dict : dict = None):
    for process, child_list in process_as_dict.items():
        # Overall process is monitored using stat
        process_usage = get_process_usage(process, read_process_stat(process))
        if LIVE_DISPLAY: print('vm', process_usage)
        if output_dict is not None and process_usage is not None: output_dict['vm'] = process_usage
        # Details of repartition is obtained using schedstat
        for child in child_list:
            child_usage = get_process_usage(child, read_process_schedstat(child))
            if LIVE_DISPLAY: print(child if (replace_pid_per_label is None or str(child) not in replace_pid_per_label) else replace_pid_per_label[str(child)], child_usage)
            if output_dict is not None and child_usage is not None: output_dict[child if (replace_pid_per_label is None or str(child) not in replace_pid_per_label) else replace_pid_per_label[str(child)]] = child_usage
        break # We only do the first VM for now

###########################################
# Read libvirt
###########################################

def read_libvirt():
    count = 0
    cpu_cumul = 0
    mem_cumul = 0
    for domain_id in VM_CONNECTOR.listDomainsID():
        try:
            virDomain = VM_CONNECTOR.lookupByID(domain_id)
            cpu_cumul+=virDomain.maxVcpus()
            mem_cumul+=int(virDomain.maxMemory()/1024)
            count+=1
        except libvirt.libvirtError as ex:  # VM is not alived anymore
            pass
    return {'libvirt_vm_count': count, 'libvirt_vm_cpu_cml': cpu_cumul, 'libvirt_vm_mem_cml': mem_cumul}

###########################################
# Read joule file, convert to watt
###########################################
def read_rapl(rapl_sysfs : dict, hist : dict, current_time : int):
    measures = dict()
    overflow = False
    package_global_joule = 0
    package_global_watt = 0
    for domain, file in rapl_sysfs.items():
        joule, watt = read_joule_file(domain=domain, file=file, hist=hist, current_time=current_time)
        if watt !=None:
            measures[domain + '-joule'] = round(joule,PRECISION)
            measures[domain + '-watt'] = round(watt,PRECISION)
            if 'package-' in domain: 
                package_global_joule += joule
                package_global_watt  += watt
        else: overflow=True

    # Track time for next round
    hist['time'] = current_time

    if measures:
        if not overflow: 
            measures['package-global-joule'] = round(package_global_joule,PRECISION)
            measures['package-global-watt']  = round(package_global_watt,PRECISION)
    return measures

def read_joule_file(domain : str, file : str, hist : dict, current_time : int):
    # Read file
    with open(file, 'r') as f: current_uj_count = int(f.read())

    # Compute delta
    current_uj_delta = current_uj_count - hist[domain] if hist[domain] != None else None
    hist[domain] = current_uj_count # Manage hist for next delta

    # Manage exceptional cases
    if current_uj_delta == None: return None, None # First call
    if current_uj_delta < 0: return None, None # Overflow

    # Convert to watt
    current_us_delta = (current_time - hist['time'])/1000 #delta with ns to us
    current_joule = current_uj_delta / (10**6) # Convert microjoules to joules (1 J = 1,000,000 µJ)
    current_watt = current_uj_delta/current_us_delta

    return current_joule, current_watt

###########################################
# I/O
###########################################

rapl_hist, cpu_hist, output_file, launch_at, last_call = {}, {}, '', 0, 0
def read_system(label : str, rapl_sysfs : dict, cpuid_per_numa : dict, cache_topo : dict, misc : dict = {}, repetition : int = 1, sleep : int = 0, init : bool = False, monitor_process_params : dict = None):
    global rapl_hist, cpu_hist, output_file, launch_at, last_call
    if init:
        rapl_hist = {name:None for name in rapl_sysfs.keys()}
        rapl_hist['time'] = None # for joule to watt conversion
        cpu_hist = {}
        launch_at = time.time_ns()
        last_call = 0
        output_file = OUTPUT_PREFIX + '-' + label + '.csv'
        with open(output_file, 'w') as f: f.write(OUTPUT_HEADER + OUTPUT_NL)

    for _ in range(repetition):
        time_to_sleep = (sleep*10**9) - (time.time_ns() - last_call) if last_call > 0 else (sleep*10**9)
        if time_to_sleep>0: time.sleep(time_to_sleep/10**9)
        else: print('Warning: overlap iteration', -(time_to_sleep/10**9), 's')
        last_call = time.time_ns()

        rapl_measures = read_rapl(rapl_sysfs=rapl_sysfs, hist=rapl_hist, current_time=last_call)
        cpu_measures  = dict()
        if PER_CACHE_USAGE:
            display_cache_usage(cputime_hist=cpu_hist, cache_topo=cache_topo)
        for key, value in read_cpu_usage(cpuid_per_numa=cpuid_per_numa, hist=cpu_hist).items(): cpu_measures[key] = value
        libvirt_measures = dict()
        if VM_CONNECTOR != None: libvirt_measures = read_libvirt()

        if monitor_process_params is not None: monitor_process(**monitor_process_params)
        output(output_file=output_file, rapl_measures=rapl_measures, cpu_measures=cpu_measures, libvirt_measures=libvirt_measures, misc=misc, time_since_launch=int((last_call-launch_at)/(10**9)))

def output(output_file : str, rapl_measures : dict, cpu_measures : dict, libvirt_measures : dict, misc : dict, time_since_launch : int):

    if LIVE_DISPLAY and rapl_measures:
        max_domain_length = len(max(list(rapl_measures.keys()), key=len))
        max_measure_length = len(max([str(value) for value in rapl_measures.values()], key=len))
        for domain, measure in rapl_measures.items():
            usage_complement = ''
            for package, cpu_usage in cpu_measures.items():
                if domain in package:
                    usage_complement+= '- ' + str(cpu_usage) + '%'
                    break
            print(domain.ljust(max_domain_length), str(measure).ljust(max_measure_length), 'W', usage_complement)
        if libvirt_measures: print('Libvirt:', libvirt_measures['libvirt_vm_count'], 'vm(s)', libvirt_measures['libvirt_vm_cpu_cml'], 'cpu(s)', libvirt_measures['libvirt_vm_mem_cml'], 'MB')
        print('---')

    # Dump reading
    with open(output_file, 'a') as f:
        for domain, measure in misc.items():
            f.write(str(time_since_launch) + ',' + domain + ',' + str(measure) + OUTPUT_NL)
        for domain, measure in rapl_measures.items():
            f.write(str(time_since_launch) + ',' + domain + ',' + str(measure) + OUTPUT_NL)
        for cpuid, measure in cpu_measures.items():
            f.write(str(time_since_launch) + ',' + cpuid + ',' + str(measure) + OUTPUT_NL)
        for metric, value in libvirt_measures.items():
            f.write(str(time_since_launch) + ',' + metric + ',' + str(value) + OUTPUT_NL)

###########################################
# Main functions
###########################################

def core_number(cpuid_per_numa : dict):
    size = 0
    for cpuid in cpuid_per_numa.values(): size+=len(cpuid)
    return size

def noise(rapl_sysfs : dict, cpuid_per_numa : dict, cache_topo : dict, label : str, load_percentage : int, monitor_process_params : dict = None):
    target_level = 0
    size = core_number(cpuid_per_numa)

    # Capture idle
    if LIVE_DISPLAY: print('gen_model target 0%')

    misc = {'phase':label,'target':0}
    if monitor_process_params is not None: monitor_process_params['output_dict'] = misc
    read_system(label=label, rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, misc=misc, repetition=MODEL_ITERATION, sleep=MODEL_MEASURE_WINDOW, init=True, monitor_process_params=monitor_process_params)

    # Capture workload
    for numa in cpuid_per_numa.keys():
        for cpuid in cpuid_per_numa[numa]:
            for _ in range(int(100/load_percentage)):
                target_level+=1
                target_level_percentage = int(round((target_level/(size/(load_percentage/100))),2)*100)
                if LIVE_DISPLAY: print(label, 'target', target_level_percentage, '%')
                process_to_kill.append(subprocess.Popen("stress-ng -c 1 -l " + str(load_percentage), shell=True, preexec_fn=setsid))

                misc={'phase':label,'target':target_level_percentage}
                if monitor_process_params is not None: monitor_process_params['output_dict'] = misc
                read_system(label=label, rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, misc=misc, repetition=MODEL_ITERATION, sleep=MODEL_MEASURE_WINDOW, init=False, monitor_process_params=monitor_process_params)
    for process in process_to_kill: killpg(getpgid(process.pid), signal.SIGTERM)

def launch_vm(label : str, host_core : int, load_percentage : int):
    estimated_duration = int(host_core * (100/load_percentage) * MODEL_MEASURE_WINDOW * MODEL_ITERATION)
    subprocess.Popen("bash/launchvm.sh " + str(host_core) + " " + str(estimated_duration), shell=True,  preexec_fn=setsid)
    process_label = {}
    MAX_TRY = 10
    for i in range(MAX_TRY):
        pid = find_process(keyword='qemu')
        if pid is not None:
            all_child_process = get_child_process(pid)
            vcpu_process = list()
            for child_process in all_child_process:
                name = get_pid_name(child_process)
                if 'CPU' in name :
                    process_label[str(child_process)] = name.replace('/KVM', '')
                    vcpu_process.append(child_process)

            print('VM found, waiting for it to be ready to serve')
            while not does_file_exist(file='/tmp/vmready-sync', to_be_removed=True):
                time.sleep(1)
            print('VM is ready to serve')
            return {pid: vcpu_process}, process_label

        print('Unable to find VM, re-trying in 15s [', i+1, '/', MAX_TRY, ']')
        time.sleep(15)
    print('Failed to launch VM on step', label,  'exiting')
    sys.exit(-1)

def gen_model(rapl_sysfs : dict, cpuid_per_numa : dict, cache_topo : dict, load_percentage : int, label : str):
    print("Launching", label)
    noise(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, load_percentage=load_percentage, label=label)

def gen_exp(rapl_sysfs : dict, cpuid_per_numa : dict, cache_topo : dict, label : str, with_noise : bool = False):
    print("Launching", label)
    # Launch VM
    process_as_dict, process_label = launch_vm(label=label, host_core=core_number(cpuid_per_numa), load_percentage=MODEL_STEP[0])
    # Capture
    if with_noise:
        monitor_process_params = {'process_as_dict':process_as_dict, 'replace_pid_per_label': process_label, 'output_dict': {}}
        noise(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, load_percentage=MODEL_STEP[0], label=label,monitor_process_params=monitor_process_params)
        return
    else:
        first_call = True
        while find_process(keyword='qemu') is not None:
            misc = {'phase':label}
            monitor_process_params = {'process_as_dict':process_as_dict, 'replace_pid_per_label': process_label, 'output_dict': misc}
            read_system(label=label, rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, misc=misc, repetition=MODEL_ITERATION, sleep=MODEL_MEASURE_WINDOW, init=first_call, monitor_process_params=monitor_process_params)
            first_call=False

###########################################
# Entrypoint, manage arguments
###########################################
if __name__ == '__main__':

    short_options = 'hlecdv:o:p:'
    long_options = ['help', 'live', 'explicit', 'cache', 'vm=', 'delay=', 'output=', 'precision=']

    try:
        arguments, values = getopt.getopt(sys.argv[1:], short_options, long_options)
    except getopt.error as err:
        print(str(err))
        print_usage()
    for current_argument, current_value in arguments:
        if current_argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        elif current_argument in('-l', '--live'):
            LIVE_DISPLAY= True
        elif current_argument in('-c', '--cache'):
            PER_CACHE_USAGE = True
        elif current_argument in('-v', '--vm'):
            import libvirt
            VM_CONNECTOR = libvirt.open(current_value)
            if not VM_CONNECTOR: raise SystemExit('Failed to open connection to ' + current_value)
        elif current_argument in('-o', '--output'):
            OUTPUT_PREFIX= current_value
        elif current_argument in('-p', '--precision'):
            PRECISION= int(current_value)

    try:
        # Find sysfs
        rapl_sysfs=find_rapl_sysfs()
        cpuid_per_numa=find_cpuid_per_numa()
        cache_topo=find_cache_topo()
        process_to_kill=list()
        print('>RAPL domain found:')
        max_domain_length = len(max(list(rapl_sysfs.keys()), key=len))
        for domain, location in rapl_sysfs.items(): print(domain.ljust(max_domain_length), location)
        print('>NUMA topology found:')
        for numa_id, cpu_list in cpuid_per_numa.items(): print('socket-' + str(numa_id) + ':', len(cpu_list), 'cores')
        print('')
        
        estimated_duration = 0
        for load_percentage in MODEL_STEP:
            estimated_duration += int(core_number(cpuid_per_numa) * (100/load_percentage) * MODEL_MEASURE_WINDOW * MODEL_ITERATION)
            break
        estimated_duration += int((core_number(cpuid_per_numa) * (100/MODEL_STEP[0]) * MODEL_MEASURE_WINDOW * MODEL_ITERATION)*2)

        print('Launching experiment', OUTPUT_PREFIX, 'with parameters:', MODEL_STEP, '%(load per step)', 'on', core_number(cpuid_per_numa), 'cores with', MODEL_ITERATION, 'measures of', MODEL_MEASURE_WINDOW, 's, expected duration:', estimated_duration, 's')
        for load_percentage in MODEL_STEP:
            gen_model(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, load_percentage=load_percentage, label='training-' + str(load_percentage))
            break

        gen_exp(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, label='groundtruth', with_noise=False)
        gen_exp(rapl_sysfs=rapl_sysfs, cpuid_per_numa=cpuid_per_numa, cache_topo=cache_topo, label='cloudlike', with_noise=True)

    except KeyboardInterrupt:
        for process in process_to_kill: killpg(getpgid(process.pid), signal.SIGTERM)
        print('Program interrupted')
        sys.exit(0)
