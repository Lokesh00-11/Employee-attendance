# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_work_hours` | It used standard Python rounding instead of half-up rounding. | If someone worked 2.5 hours, standard rounding makes it 2 hours, which messes up their pay. | I used the Python `decimal` library to make sure it always rounds half-up. |
| 2 | `compute_overtime` | It just gave everyone exactly 60 minutes of overtime no matter what. | Every single punch-out record would say the person worked 1 hour of overtime, even if they left early. | I wrote math to calculate the exact minutes between the end of their shift and when they punched out, and only count it if it's over 30 minutes. |
| 3 | `compute_late_minutes` | It didn't give people the 10-minute grace period. | If someone showed up 5 minutes late, they would be marked late immediately instead of being given a pass. | I added a check so it only marks them late if they are strictly more than 10 minutes late. |
| 4 | `create_employee` | It didn't check if the shift start and end times were the exact same. | You could accidentally make an employee with a shift from 09:00 to 09:00, which makes no sense. | I added a quick check to throw an error if the start and end times match. |
| 5 | `list_employees` | It tried to load every single employee into memory before sorting them. | If the company had thousands of employees, the server would run out of memory and crash. | I made the database handle the sorting and paging so the server doesn't get overloaded. |
| 6 | `punch_in` | It just grabbed the computer's local time with milliseconds attached. | The times would be slightly wrong depending on where the server is, and the extra milliseconds would break things. | I made sure it grabs the proper UTC time and chops off the milliseconds before saving. |
| 7 | `punch_in` | It checked if the record existed and then inserted it, which is unsafe. | If a user double-clicked the punch-in button really fast, it would create two records for them on the same day. | I let the database handle the unique rule and just catch the error if it blocks a duplicate. |

Also note anything you looked at and decided was **not** a defect, and why.
- **Missing database IDs:** The starter code throws away the `_id` before returning the data. I decided this wasn't a bug because the instructions clearly state we should use the employee code and date as the main keys instead of a random database ID.
