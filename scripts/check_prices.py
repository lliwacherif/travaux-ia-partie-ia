import json

data = json.load(open('bibliotheque-travaux-ia-v1.json', 'r', encoding='utf-8'))
lines = data.get('lines', [])

keywords = ['couverture', 'climatisation', 'facade', 'façade', 'isolation therm', 'cuisine']
count = 0
for l in lines:
    cm = l.get('corps_metier', '').lower()
    if any(k in cm for k in keywords):
        desig = l.get('designation', '')[:70]
        prix = l.get('prix_unitaire_ht', 0)
        unite = l.get('unite', '?')
        print(f"{prix:8.2f} {unite:6s} | {desig}")
        count += 1
        if count >= 30:
            break
