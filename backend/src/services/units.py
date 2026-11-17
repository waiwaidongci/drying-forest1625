'''单位换算核心：实验室现行单位为开尔文(K)与秒(s)。
旧数据为摄氏度(℃)/分钟(min)，新设备可按华氏度(℉)申报；每批声明单位并保留原值。'''
TEMP_UNITS=('C','F','K')
TIME_UNITS=('min','s')
LEGACY_PROFILE='C/min'
CANONICAL_PROFILE='K/s'
UNIT_PROFILES=tuple(t+'/'+m for t in TEMP_UNITS for m in TIME_UNITS)
FORMULAS=[
    {'id':'c_to_k','label':'摄氏度 → 开尔文','expression':'K = ℃ + 273.15'},
    {'id':'f_to_k','label':'华氏度 → 开尔文','expression':'K = (℉ − 32) × 5/9 + 273.15'},
    {'id':'k_to_c','label':'开尔文 → 摄氏度（回核）','expression':'℃ = K − 273.15'},
    {'id':'min_to_s','label':'分钟 → 秒','expression':'s = min × 60'},
    {'id':'s_to_min','label':'秒 → 分钟（回核）','expression':'min = s ÷ 60'},
]
def parse_profile(unit):
    if not unit or '/' not in str(unit):return None
    t,_,m=str(unit).partition('/')
    if t not in TEMP_UNITS or m not in TIME_UNITS:return None
    return t,m
def temperature_to_kelvin(value,unit):
    if unit=='C':return value+273.15
    if unit=='F':return (value-32)*5/9+273.15
    return value
def kelvin_to_temperature(value,unit):
    if unit=='C':return value-273.15
    if unit=='F':return (value-273.15)*9/5+32
    return value
def duration_to_seconds(value,unit):return value*60 if unit=='min' else value
def seconds_to_duration(value,unit):return value/60 if unit=='min' else value
def to_canonical(values,profile):
    '''申报值 → 现行 K/s；profile 必须已通过 parse_profile 校验。'''
    t,m=parse_profile(profile)
    out=dict(values)
    out['temperature']=temperature_to_kelvin(float(values['temperature']),t)
    out['minutes']=duration_to_seconds(float(values['minutes']),m)
    return out
def from_canonical(values,profile):
    '''现行 K/s → 目标单位（等价输入回核/旧量纲展示）。'''
    t,m=parse_profile(profile)
    out=dict(values)
    out['temperature']=kelvin_to_temperature(float(values['temperature']),t)
    out['minutes']=seconds_to_duration(float(values['minutes']),m)
    return out
