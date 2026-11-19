* Includes resolved relative to the circuit (.cir) directory.
.include ../models/p1_opamp.lib
* All independent SI-valued parameters are specified by each case.
Vcc vcc 0 5
Rmid_top vcc mid_raw 10k
Rmid_bottom mid_raw 0 10k
Cmid mid_raw 0 10u
Xmid mid_raw vmid vcc 0 vmid P1_OP
* A1=+10mV, B=-10mV, A2=+10mV by default. All relative to ZERO modeled OCP.
* These are fixed electrical stimuli, NOT the P3 adaptive state machine.
Vrequest req 0 PWL(0 0 2 0 2.001 {AMP} 14 {AMP} 14.001 0 20 0 20.001 {-AMP} 32 {-AMP} 32.001 0 38 0 38.001 {AMP} 50 {AMP} 50.001 0 56 0)
* Positive e = V(WE)-V(RE); positive current flows from WE through cell to RE.
Btarget target 0 V=V(vmid)-V(req)
Xref re re_buf vcc 0 re_buf P1_OP
Xcontrol target re_buf vcc 0 drive P1_OP
* Drive disconnect test. Not an OCP-isolating cell switch or actual relay coil.
Brelay relay 0 V=if(SWFAULT>0,if(time<10,1,if(time<10.02,0,1)),1)
Sdrive drive ce relay 0 P1_SWITCH
.model P1_SWITCH SW(Ron=1 Roff=1T Vt=0.5 Vh=0.05)
Rcounter ce re 100
* Ideal CE boundary plus series drive resistance; RE senses solution potential.
* No counter-electrode electrochemistry or spatial electrolyte field model.
Vsense we metal 0
Rpolar metal surf {RP}
Cdouble metal surf {CDL}
Runcomp surf re {RU}
* Transimpedance amplifier supplies positive WE->RE current via feedback.
Xtia vmid we vcc 0 tia P1_OP
Rfeedback tia we {RF}
Cfeedback tia we 100p
* Measurement-path gain/offset fault: after TIA, before ADC input filter.
* This is a modeled instrumentation fault, not a claim about a physical resistor.
Vnoise nunit 0 PWL FILE=../stimuli/noise_seed42.txt
Bcondition conditioned 0 V=V(vmid)+(1+GAINSTEP*if(time>8,1,0))*(V(tia)-V(vmid))+OFFSET*if(time>8,1,0)+NOISE*V(nunit)
Rfilter conditioned adc {RADC}
Cfilter adc 0 {CADC}
Radcload adc 0 1G
* Defined extra charge-injection stimulus: 1uA for 1ms = 1nC when enabled.
Iinject 0 re PULSE(0 {SWFAULT*1u} 10 1u 1u 1m 100)
* Export helpers: numeric V(i_true)/V(i_meas) values represent AMPERES.
* Nominal Rf is deliberately used to reconstruct current even under gain fault.
Bactual actual 0 V=V(we,re)
Binterface eta 0 V=V(metal,surf)
Btrue i_true 0 V=I(Vsense)
Bmeasured i_meas 0 V=(V(adc)-V(vmid))/RF
.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n
.save V(req) V(actual) V(i_true) V(i_meas) V(vmid) V(tia) V(adc) V(relay) V(eta) V(ce) V(re) V(we)
.tran 0 56 0 1m
.meas tran epos AVG V(actual) FROM=13 TO=14
.meas tran eneg AVG V(actual) FROM=31 TO=32
.meas tran ipos_true AVG V(i_true) FROM=13 TO=14
.meas tran ineg_true AVG V(i_true) FROM=31 TO=32
.meas tran ipos_meas AVG V(i_meas) FROM=13 TO=14
.meas tran ineg_meas AVG V(i_meas) FROM=31 TO=32
.meas tran rdc_true PARAM (epos-eneg)/(ipos_true-ineg_true)
.meas tran rdc_meas PARAM (epos-eneg)/(ipos_meas-ineg_meas)
.meas tran rp_meas PARAM rdc_meas-RU
.meas tran vmid_dc AVG V(vmid) FROM=1 TO=1.5
.meas tran max_adc MAX V(adc)
.meas tran min_adc MIN V(adc)
