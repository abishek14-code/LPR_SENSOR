Version 4
SymbolType CELL
RECTANGLE Normal -128 -48 128 48
TEXT 0 0 Center 1 (Vhold - Vmid) / RF
LINE Normal -160 -24 -128 -24
TEXT -120 -24 Left 0 adc_hold
LINE Normal -160 24 -128 24
TEXT -120 24 Left 0 vmid
LINE Normal 128 0 160 0
LINE Normal 0 48 0 80
WINDOW 0 0 -96 Center 2
WINDOW 3 0 104 Invisible 0
SYMATTR Prefix X
SYMATTR Value LPR_CURRENT
PIN -160 -24 NONE 0
PINATTR PinName adc_hold
PINATTR SpiceOrder 1
PIN -160 24 NONE 0
PINATTR PinName vmid
PINATTR SpiceOrder 2
PIN 160 0 NONE 0
PINATTR PinName i_meas
PINATTR SpiceOrder 3
PIN 0 80 NONE 0
PINATTR PinName gnd
PINATTR SpiceOrder 4
