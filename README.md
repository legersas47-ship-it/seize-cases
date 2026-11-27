# Seize Cases

Les visiteurs envoient une photo depuis le site (GitHub Pages, dossier `docs/`).
L'ordinateur la reçoit, la transforme (trame comics, éco, lignes, grain, contour, fantôme, pop art)
avec les encres encore disponibles, et l'imprime dans **1/16 de page**, sur la case suivante de la même feuille.

## Lancer
Double-clic sur `LANCER.bat`. Le panneau de contrôle s'ouvre sur http://127.0.0.1:8766.
Le script ouvre un tunnel public (cloudflared) et publie automatiquement son adresse dans `docs/config.json`.

## Rituel de la feuille
1. Mets **une seule feuille** dans le bac (face à imprimer et sens toujours identiques ; fais une marque au crayon dans un coin).
2. Une photo arrive → impression dans la case suivante → la feuille ressort.
3. Recharge la même feuille dans le même sens. Tant que le bac est vide, l'imprimante attend : la photo suivante ne part qu'une fois la précédente terminée.
4. Après la case 16 : pause automatique. Mets une feuille vierge et clique « Reprendre ».

Ordre des cases : de gauche à droite, de haut en bas (1 = en haut à gauche).
Imprime d'abord la grille de calibration (bouton du panneau) pour vérifier.

## Encres
Dans le panneau, coche les encres qui restent. Avec seulement le noir : impression en niveaux de gris (une seule cartouche utilisée).
La DeskJet 3700 sait imprimer avec une seule cartouche : retire la cartouche vide et elle passe en « mode cartouche unique ».
