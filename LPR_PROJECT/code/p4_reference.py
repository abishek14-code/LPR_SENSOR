"""Independent closed-form reference. Never reads FEM/native/P3 outputs."""
import math

EQUATIONS={
 'bulk_current_A':'I = sigma * width_m * depth_m * delta_V / electrolyte_length_m',
 'Ru_ohm':'Ru = reference_distance_m / (sigma * width_m * depth_m)',
 'Rdc_ohm':'Rdc = Ru + Rp',
 'tau_s':'tau = Ru*Rp/(Ru+Rp)*Cdl',
 'initial_current_A':'I0 = perturbation_V/Ru',
 'steady_current_A':'Iinf = perturbation_V/(Ru+Rp)',
 'initial_condition':'eta(t)=eta_inf+(eta_start-eta_inf)*exp(-t/tau); I=(E-eta)/Ru',
 'ocp_decay':'drive open: eta(t)=eta_start*exp(-t/(Rp*Cdl)); external current=0',
 'polarization_resistance':'Rp_offline = Rmeasured - independently specified/evaluated Ru; not an online input'
}

def spatial(spec,sigma):
    w=spec['width_m'];d=spec['depth_m'];length=spec['electrolyte_length_m'];r=spec['reference_distance_m']
    assert 0<r<length and min(w,d,sigma)>0
    return {'bulk_current_A':sigma*w*d/length,
            'bulk_solution_resistance_ohm':length/(sigma*w*d),
            'Ru_ohm':r/(sigma*w*d),'counter_to_reference_ohm':(length-r)/(sigma*w*d),
            'reference_fraction_of_bulk':r/length}

def cell(ru,rp,cdl,step=.01):
    assert min(ru,rp,cdl)>0
    return {'Rdc_ohm':ru+rp,'tau_s':ru*rp/(ru+rp)*cdl,
            'initial_current_A':step/ru,'steady_current_A':step/(ru+rp),
            'open_circuit_interface_tau_s':rp*cdl}

def advance(eta,command,duration,ru,rp,cdl,drive=True):
    if not drive:return eta*math.exp(-duration/(rp*cdl)),0.
    tau=ru*rp/(ru+rp)*cdl;inf=command*rp/(ru+rp)
    end=inf+(eta-inf)*math.exp(-duration/tau)
    return end,(command-end)/ru
