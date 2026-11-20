"""Owned-PID RSS/CPU and bounded clock witnesses; no independent host attestation."""

import ctypes
import os
import platform
import subprocess
import time


class Clocks:
    def __init__(self):
        self.kind = 'monotonic'
        self.continuous = time.monotonic
        if platform.system() == 'Darwin':
            library = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            class Timebase(ctypes.Structure):
                _fields_ = [('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]
            base = Timebase()
            library.mach_timebase_info(ctypes.byref(base))
            library.mach_continuous_time.restype = ctypes.c_uint64
            self.continuous = lambda:library.mach_continuous_time()*base.numer/base.denom/1e9
            self.kind = 'mach_continuous_time'
        elif hasattr(time,'CLOCK_BOOTTIME'):
            self.continuous = lambda:time.clock_gettime(time.CLOCK_BOOTTIME)
            self.kind = 'CLOCK_BOOTTIME'

    def sample(self):
        before = time.monotonic()
        row = {'wall_unix':time.time(),'monotonic':time.monotonic(),'continuous':self.continuous(),
               'continuous_kind':self.kind}
        row['bracket_seconds'] = time.monotonic()-before
        return row


def sample_processes(roles):
    pids = sorted(roles)
    if not pids:
        return []
    value = subprocess.run(['/bin/ps','-o','pid=,rss=,%cpu=,lstart=','-p',','.join(map(str,pids))],
                           capture_output=True,text=True,timeout=3,check=False)
    result = []
    for line in value.stdout.splitlines():
        parts = line.split(None,3)
        if len(parts) == 4:
            pid,rss,cpu,birth = parts
            result.append({'pid':int(pid),'role':roles[int(pid)],'rss_bytes':int(rss)*1024,
                'cpu_percent_ps':float(cpu),'os_birth_lstart':birth})
    found = {r['pid'] for r in result}
    for pid in pids:
        if pid not in found:
            result.append({'pid':pid,'role':roles[pid],'rss_bytes':None,'cpu_percent_ps':None,'os_birth_lstart':None})
    return result
