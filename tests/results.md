# WAKEEL trial results

Test file: `tests\test_deliveries.json`  
Engine: **claude** · Cases: **19** · Passed: **19/19**  
Planted problems caught: **23/23** · No-touch share: **17/19** (reached the confirm screen with no question) · Avg agent time: **10.16s**

| Case | Scenario | Result | Question asked | Planted problems caught | Notes |
|---|---|---|---|---|---|
| T01 | Brief demo: damaged monitor, missing mouse | PASS | no | damaged ✓, missing ✓ |  |
| T02 | Same delivery, different dialect wording | PASS | no | damaged ✓, missing ✓ |  |
| T03 | All good: full delivery closes the bill | PASS | no | - |  |
| T04 | Partial delivery: 6 of 10 chairs | PASS | no | partial ✓, missing ✓ |  |
| T05 | Wrong model: Samsung monitors instead of Dell | PASS | no | wrong_model ✓ |  |
| T06 | One delivery, two bills (keyboards + laptops) | PASS | no | split ✓ |  |
| T07 | Same item on two bills, nothing to tell them apart: agent asks | PASS | yes | ambiguous ✓, partial ✓ |  |
| T08 | Same item on two bills, quantity decides | PASS | no | missing ✓ |  |
| T09 | Same item on two bills, other items decide | PASS | no | - |  |
| T10 | Item already received earlier: possible duplicate | PASS | no | duplicate ✓ |  |
| T11 | Same delivery reported twice | PASS | no | duplicate ✓ |  |
| T12 | Gate keeper names the bill number | PASS | no | missing ✓ |  |
| T13 | English input, damaged projector | PASS | no | damaged ✓ |  |
| T14 | Partial + damaged laptops, hold above manager threshold | PASS | no | damaged ✓, partial ✓ |  |
| T15 | Item not on any open bill | PASS | no | unexpected_item ✓ |  |
| T16 | Simulated RFID scan (no typing) | PASS | no | - |  |
| T17 | Supplier not said: agent asks which company | PASS | yes | missing ✓ |  |
| T18 | More units than one bill holds: split across bills | PASS | no | split ✓, missing ✓ |  |
| T19 | Over-delivery beyond all open bills | PASS | no | duplicate ✓, missing ✓ |  |
