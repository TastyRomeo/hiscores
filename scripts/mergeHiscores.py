import csv
import os
import time

hiscores_old_path = "data/hiscores.csv"
hiscores_new_path = "data/hiscores-new.csv"
oldHS = {}
newHS = {}
header= []

with open(hiscores_old_path, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    for r in rows[1:]:
        if r:
            oldHS[r[0]] = r

with open(hiscores_new_path, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    # Pick header from new version in case of changes to script!
    header = rows[0]
    for r in rows[1:]:
        if r:
            newHS[r[0]] = r

# Add in missing old data!
for user in oldHS.keys():
    if user in newHS:
        if oldHS[user][:-1] > newHS[user][:-1]:
            # Somehow previous line is more recent (e.g. due to concurrent updates?)
            newHS[user] = oldHS[user]
    else:
        now = int(time.time())
        upd = int(oldHS[user][:-1])
        if now - upd <= 1209600:
            newHS[user] = oldHS[user]
        else:
            print(f"Removing data for user '{user}' since data is >2 weeks old")

# Write back in sorted order
with open(hiscores_old_path, "w+", newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(header)
    for user in sorted(newHS.keys(), key=str.lower):
        writer.writerow(newHS[user])

os.remove(hiscores_new_path)
