# apps4athens-data-logger

Μαζεύει ιστορικό από ανοιχτές πηγές της Αθήνας που δίνουν **μόνο την τρέχουσα κατάσταση**, ώστε στο [Apps4Athens 3.0 Gov Hackathon](https://apps4athens.gr/gov-innovation/) (21–22/11/2026) να υπάρχουν εβδομάδες δεδομένων. Ξεκίνησε 21/10/2026.

| Πηγή | Συχνότητα | Τι κρατάμε | Άδεια πηγής |
|---|---|---|---|
| `traffic`: [attica-region-traffic-data](https://data.gov.gr/dataset/attica-region-traffic-data) (Περιφέρεια Αττικής, ~620 σταθμοί μέτρησης) | 1 λεπτό | `id, vehicle, timestamp, count, occ, speed`, μόνο νέες μετρήσεις. Μία φορά τη μέρα και πλήρες snapshot με συντεταγμένες και οδούς στο `meta/` | CC BY 4.0 |
| `elevators`: [stasy.gr/elevators](https://www.stasy.gr/elevators/) (71 σταθμοί, ~215 ανελκυστήρες) | 10 λεπτά | κατάσταση ανά σταθμό **και** ανά ανελκυστήρα | όροι ιστοτόπου ΣΤΑΣΥ (δεν δηλώνεται άδεια) |
| `chargers`: [ΜΥΦΑΗ](https://electrokinisi.yme.gov.gr/public/HelpMyfah/PublicData/) (όλη η Ελλάδα, ~10k EVSE, OCPI 2.2) | 10 λεπτά | μόνο **αλλαγές** κατάστασης ανά EVSE. Μία φορά τη μέρα και το static αρχείο (τοποθεσίες, συντεταγμένες) στο `meta/` | δεν αναγράφεται |

Ο κώδικας είναι MIT. Τα δεδομένα ανήκουν στους φορείς-πηγές και ισχύουν οι δικοί τους όροι.

## Πώς τρέχει

**GitHub Actions (κύριος τρόπος, 24/7, για `traffic` και `chargers`).** Το stasy.gr απαντά 403 στις IP του GitHub, οπότε οι **ανελκυστήρες καταγράφονται μόνο τοπικά**. Το [`.github/workflows/logger.yml`](.github/workflows/logger.yml) τρέχει το `collect.py loop` σε 5 ενότητες των ~68 λεπτών ανά run. Κάθε ενότητα ανεβάζει τα δεδομένα της ως **artifact** `data-<run>-<n>` με διατήρηση 90 ημερών. Τα δεδομένα δεν γίνονται commit στο repo. Ένα cron κάθε ώρα, μαζί με το `concurrency`, κρατά ένα run ενεργό και ένα σε αναμονή, ώστε το επόμενο να ξεκινά μόλις τελειώσει το προηγούμενο.

**Τοπικά (εφεδρικός τρόπος, Windows Task Scheduler).** Εργασίες στον φάκελο `\Apps4Athens\`: `traffic` κάθε 1′, `elevators` και `chargers` κάθε 10′, μέχρι ~5/12/2026. Τρέχουν μόνο όσο ο υπολογιστής είναι ανοιχτός. Γράφουν στο `data/`.

```powershell
Get-ScheduledTask -TaskPath "\Apps4Athens\" | Get-ScheduledTaskInfo                      # κατάσταση
Get-ScheduledTask -TaskPath "\Apps4Athens\" | Disable-ScheduledTask                      # παύση
Get-ScheduledTask -TaskPath "\Apps4Athens\" | Unregister-ScheduledTask -Confirm:$false   # αφαίρεση
```

## Συγκέντρωση των δεδομένων

```bash
python merge.py              # κατεβάζει τα νέα artifacts στο cloud/ και ενώνει cloud + τοπικά στο merged/
python merge.py --cloud-only # μόνο τα δεδομένα του GitHub
```

Αποτέλεσμα: `merged/<source>/YYYY-MM-DD.csv.gz` (ημέρες σε UTC, ταξινομημένα, χωρίς διπλοεγγραφές) και `merged/coverage.txt` με τα λεπτά κάλυψης ανά ημέρα. Διαβάζονται με `pandas.read_csv(path)`. Για τον Δήμο Αθηναίων στο traffic: `dimos == "ΑΘΗΝΑΙΩΝ"` στο `meta/` (168 σταθμοί).

Χειροκίνητα: `python collect.py traffic|elevators|chargers` ή `python collect.py loop 10`. Μόνο stdlib, Python 3.9+. Οι φάκελοι ρυθμίζονται με `A4A_DATA`, `A4A_STATE`, `A4A_LOGS`.
