# WAKEEL trial results

Test file: `tests/team_cases.json`  
Engine: **offline** · Cases: **17** · Passed: **11/17**  
Planted problems caught: **13/18** · No-touch share: **16/17** (reached the confirm screen with no question) · Avg agent time: **0.01s**

| Case | Scenario | Result | Question asked | Planted problems caught | Notes |
|---|---|---|---|---|---|
| H01 | Printer and desk only, rest later | PASS | no | missing ✓ |  |
| H02 | Two chairs broken ("فيهم كسر") | FAIL | no | damaged ✗ | B-2001: pay/hold 6900/0 != 6000/900; B-2001 CHR: received != damaged |
| H03 | "Batteries" UPS and two routers, all fine | PASS | no | - |  |
| H04 | Cables short: only five came | FAIL | no | partial ✗ | bills [] != expected ['B-2007'] |
| H05 | Laptop with a broken screen, mice didn't come | PASS | no | damaged ✓, missing ✓ |  |
| H06 | The last item of a half-received bill | PASS | no | - |  |
| H07 | Switches of another brand than ordered | FAIL | no | wrong_model ✗, missing ✓ | C-3005: pay/hold 1900/700 != 0/2600; C-3005 SWT: received != wrong_model |
| H08 | Eight chairs (two scratched) and one of two desks | FAIL | no | damaged ✗, partial ✓ | B-2001: pay/hold 4800/2100 != 3900/3000; B-2001 CHR: partial != damaged |
| H09 | English: keyboards + the two Dell monitors | FAIL | no | - | A-1038: pay/hold 1800/750 != 2550/0; A-1038 KBD: wrong_model != received |
| H10 | Driver says the rest comes next week | PASS | no | missing ✓ |  |
| H11 | Two of three UPS units | PASS | no | partial ✓, missing ✓ |  |
| H12 | A box with ten HDMI cables | PASS | no | missing ✓ |  |
| H13 | A third router (only two were ordered, both received) | PASS | no | duplicate ✓ |  |
| H14 | "The mouse": on two bills, agent must ask | PASS | yes | ambiguous ✓, missing ✓ |  |
| H15 | Everything on the laptop bill, all fine | PASS | no | - |  |
| H16 | Router + two switches: other items pick the bill | PASS | no | - |  |
| H17 | English: cracked UPS case, routers not delivered | FAIL | no | damaged ✗, missing ✓ | C-3001: pay/hold 3300/1400 != 2200/2500; C-3001 UPS: received != damaged |
