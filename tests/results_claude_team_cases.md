# WAKEEL trial results

Test file: `tests/team_cases.json`  
Engine: **claude** · Cases: **17** · Passed: **16/17**  
Planted problems caught: **17/18** · No-touch share: **15/17** (reached the confirm screen with no question) · Avg agent time: **9.64s**

| Case | Scenario | Result | Question asked | Planted problems caught | Notes |
|---|---|---|---|---|---|
| H01 | Printer and desk only, rest later | PASS | no | missing ✓ |  |
| H02 | Two chairs broken ("فيهم كسر") | PASS | no | damaged ✓ |  |
| H03 | "Batteries" UPS and two routers, all fine | PASS | no | - |  |
| H04 | Cables short: only five came | PASS | no | partial ✓ |  |
| H05 | Laptop with a broken screen, mice didn't come | PASS | no | damaged ✓, missing ✓ |  |
| H06 | The last item of a half-received bill | PASS | no | - |  |
| H07 | Switches of another brand than ordered | PASS | no | wrong_model ✓, missing ✓ |  |
| H08 | Eight chairs (two scratched) and one of two desks | PASS | no | damaged ✓, partial ✓ |  |
| H09 | English: keyboards + the two Dell monitors | PASS | no | - |  |
| H10 | Driver says the rest comes next week | PASS | no | missing ✓ |  |
| H11 | Two of three UPS units | PASS | no | partial ✓, missing ✓ |  |
| H12 | A box with ten HDMI cables | PASS | no | missing ✓ |  |
| H13 | A third router (only two were ordered, both received) | FAIL | yes | duplicate ✗ | expected question=False, got True; flags [] != ['duplicate'] |
| H14 | "The mouse": on two bills, agent must ask | PASS | yes | ambiguous ✓, missing ✓ |  |
| H15 | Everything on the laptop bill, all fine | PASS | no | - |  |
| H16 | Router + two switches: other items pick the bill | PASS | no | - |  |
| H17 | English: cracked UPS case, routers not delivered | PASS | no | damaged ✓, missing ✓ |  |
