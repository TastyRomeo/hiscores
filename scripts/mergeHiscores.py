import csv
import os

hiscores_old_path = "data/hiscores.csv"
hiscores_new_path = "data/hiscores-new.csv"
oldHS = {}
newHS = {}
header=[]

with open(hiscores_old_path, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    header = rows[0]
    for r in rows[1:]:
        oldHS[r[0]] = r

with open(hiscores_new_path, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    rows = list(reader)
    for r in rows[1:]:
        newHS[r[0]] = r

# Add in missing old data!
for user in oldHS.keys():
    if (user not in newHS) or (oldHS[user][:-1] > newHS[user][:-1]):
        newHS[user] = oldHS[user]

# Write back in sorted order
with open(hiscores_old_path, "w+", newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(header)
    for user in sorted(newHS.keys(), key=str.lower):
        writer.writerow(newHS[user])

os.remove(hiscores_new_path)
