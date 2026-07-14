"""
Hexing meter -> contactor controller  —  SKiDL netlist (THT / industrial)
-------------------------------------------------------------------------
Generates a KiCad netlist (board.net). Then in KiCad: import netlist, place,
ROUTE WITH THE ISOLATION GAP ENFORCED (see notes), and Plot -> Gerbers.

Setup:
    pip install skidl
    (KiCad symbol/footprint libraries must be installed)
    python board.py        ->  board.net

Adjust symbol/footprint library names to your KiCad version if ERC complains.
The ESP32-C3 Super Mini has no standard symbol, so it is represented as two
1x08 pin-header sockets; edit the pin-index mapping to match your module.
"""
from skidl import *

# ---------------- nets ----------------
p5_esp  = Net('+5V_ESP');   gnd_esp = Net('GND_ESP');   p3v3 = Net('+3V3')
p5_opto = Net('+5V_OPTO');  gnd_opt = Net('GND_OPTO')
on_sense  = Net('ON_SENSE')    # -> GPIO1
off_sense = Net('OFF_SENSE')   # -> GPIO3
ssr_ctrl  = Net('SSR_CTRL')    # -> GPIO10
on_led_k  = Net('ON_LED_K')    # opto A cathode -> COM(on)
off_led_k = Net('OFF_LED_K')   # opto B cathode -> COM(off)

# ---------------- helper builders ----------------
def R(val, ref):
    return Part('Device', 'R', value=val, ref=ref,
        footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')

def TB2(ref):
    return Part('Connector', 'Screw_Terminal_01x02', ref=ref,
        footprint='TerminalBlock:TerminalBlock_bornier-2_P5.08mm')

# ---------------- parts ----------------
# ESP32-C3 Super Mini socket (two 8-pin header rows). Edit mapping below.
esp = Part('Connector_Generic', 'Conn_01x08', ref='J_ESP',
        footprint='Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical')

u_on  = Part('Isolator', 'PC817', ref='U_ON',
        footprint='Package_DIP:DIP-4_W7.62mm')
u_off = Part('Isolator', 'PC817', ref='U_OFF',
        footprint='Package_DIP:DIP-4_W7.62mm')

r_on_led  = R('330', 'R1');  r_off_led = R('330', 'R2')
r_on_pu   = R('10k', 'R3');  r_off_pu  = R('10k', 'R4')

c_bulk = Part('Device', 'C', value='470uF', ref='C1',
        footprint='Capacitor_THT:CP_Radial_D8.0mm_P3.50mm')
c_dec  = Part('Device', 'C', value='100nF', ref='C2',
        footprint='Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm')

tb_pesp = TB2('TB1')   # +5V_ESP , GND_ESP     (HiLink #1)
tb_popt = TB2('TB2')   # +5V_OPTO, GND_OPTO    (HiLink #2)
tb_ssr  = TB2('TB4')   # SSR_CTRL , GND_ESP     -> SSR DC input (3,4)
tb_mtr  = Part('Connector', 'Screw_Terminal_01x04', ref='TB3',
        footprint='TerminalBlock:TerminalBlock_bornier-4_P5.08mm')  # ON,COMon,OFF,COMoff

# ---------------- opto ON channel ----------------
p5_opto     += r_on_led[1]
r_on_led[2] += u_on['A']            # anode (pin1)
u_on['K']   += on_led_k             # cathode (pin2) -> COM(on)
u_on['C']   += on_sense, r_on_pu[1] # collector (pin4)
r_on_pu[2]  += p3v3
u_on['E']   += gnd_esp              # emitter (pin3)

# ---------------- opto OFF channel ----------------
p5_opto      += r_off_led[1]
r_off_led[2] += u_off['A']
u_off['K']   += off_led_k
u_off['C']   += off_sense, r_off_pu[1]
r_off_pu[2]  += p3v3
u_off['E']   += gnd_esp

# ---------------- meter terminal block ----------------
tb_mtr[1] += gnd_opt      # ON  terminal   -> GND_OPTO
tb_mtr[2] += on_led_k     # COM(on)        -> opto A cathode
tb_mtr[3] += gnd_opt      # OFF terminal   -> GND_OPTO
tb_mtr[4] += off_led_k    # COM(off)       -> opto B cathode

# ---------------- power + SSR + decoupling ----------------
tb_pesp[1] += p5_esp;  tb_pesp[2] += gnd_esp
tb_popt[1] += p5_opto; tb_popt[2] += gnd_opt
tb_ssr[1]  += ssr_ctrl; tb_ssr[2] += gnd_esp
c_bulk[1]  += p5_esp;  c_bulk[2] += gnd_esp
c_dec[1]   += p3v3;    c_dec[2]  += gnd_esp

# ---------------- ESP header mapping (EDIT to your module pinout) ----------------
esp[1] += p5_esp
esp[2] += gnd_esp
esp[3] += p3v3
esp[4] += on_sense     # GPIO1
esp[5] += off_sense    # GPIO3
esp[6] += ssr_ctrl     # GPIO10
# esp[7], esp[8] -> spare GPIOs / NC

ERC()
generate_netlist(file_='board.net')
"""
NOTES FOR LAYOUT (do not skip — this is a mains-isolation board):
- Keep GND_OPTO copper and GND_ESP copper as SEPARATE pours that never touch.
- Under each PC817, keep >=8 mm creepage between the LED side (pins 1/2, on
  GND_OPTO / meter) and the transistor side (pins 3/4, on GND_ESP). Mill a slot
  in the board across that gap to guarantee it.
- Same >=8 mm gap between TB3 (meter, mains-referenced) and the logic section.
- The SSR is an external panel brick; only its low-voltage DC input (2 wires)
  comes to TB4, so no mains switching sits on this PCB. Good — keep it that way.
- Screw terminals on the board edges; ESP + optos + Rs in the compact middle.
"""
