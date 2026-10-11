# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
ANS: 
- I made a unique index on `emp_code` so no two employees can have the same ID.
- I made a unique index on `emp_code` and `date` in the attendance logs so a person can't punch in twice on the exact same day.
- I added an index on `date` and `emp_code` to make the attendance list load and sort faster.
- I thought about adding an index on `status`, but since there are only a few options (like PRESENT or ABSENT), it wouldn't speed anything up, so I skipped it.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
ANS: I didn't write code that checks if a record exists and then inserts it, because that causes race conditions if two clicks happen at the exact same time. Instead, I just tell the database to insert the punch-in directly. Because I set up a rule in the database that you can only have one punch-in per day, the database blocks the second request automatically. My code just catches that database error and safely sends back a 409 Conflict message.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
ANS: It returns both of them because they tied. I handle the ranking in Python. For example, if two people tie for 1st place, they both get rank 1, and the next person drops to rank 3. If the limit is 2, my code will stop before getting to rank 3, so it just returns the two people who tied for 1st place.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
ANS: When I build the query, I start by pulling everyone from the employees collection first, and then I link their attendance logs to them. Since I start with the employees list, people with zero logs are still included in the results and get counted in the total headcount.

5. **One thing you would change** if this had to serve 100x the data.
ANS: Right now, the analytics endpoints do a lot of heavy math every time someone loads the page. If we had 100x more data, this would be too slow. I would change it so that a background job calculates these numbers once a night and saves them to a new table. Then the API would just read those saved numbers quickly instead of doing the math on the fly.
