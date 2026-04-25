# =============================================================================
# PROCESSEUR DE SIGNAUX DE SYNCHRONISATION VIDÉO - RP2040 / MicroPython
# =============================================================================
#
# CE QUE FAIT CE PROGRAMME :
# Ce code tourne sur un microcontrôleur RP2040 (ex : Raspberry Pi Pico).
# Il reçoit un signal de synchronisation composite (C-Sync) issu d'une source
# vidéo, en extrait les synchronisations horizontale (H-Sync) et verticale
# (V-Sync), puis applique un décalage de phase (déphasage) réglable sur ces
# signaux — un peu comme déplacer l'image sur l'écran.
#
# Le réglage du déphasage se fait via deux potentiomètres (un pour l'axe
# horizontal, un pour le vertical), et la valeur peut être mémorisée en
# mémoire flash via un bouton.
#
# ARCHITECTURE MATÉRIELLE UTILISÉE :
# ┌────────────────────────────────────────────────────────────────┐
# │  RP2040                                                        │
# │                                                                │
# │  GP26 ──── Potentiomètre Horizontal (ADC)                      │
# │  GP27 ──── Potentiomètre Vertical   (ADC)                      │
# │  GP8  ──── Bouton mémoire (entrée, pull-up)                    │
# │                                                                │
# │  GP0  ──── C-Sync entrée (signal composite à analyser)         │
# │  GP1  ──── H-Sync extrait (sortie)                             │
# │  GP2  ──── V-Sync extrait (sortie)                             │
# │  GP3  ──── H-Sync déphasé (sortie)                             │
# │  GP4  ──── V-Sync déphasé (sortie)                             │
# │  GP5  ──── C-Sync reconstruit (sortie)                         │
# └────────────────────────────────────────────────────────────────┘
#
# MACHINES D'ÉTAT PIO UTILISÉES (6 au total) :
#   sm4 (SM0) : hsync_flywheel   → génère le H-Sync (PIO bloc 0, SM 0)
#   sm5 (SM1) : vsync_pio        → extrait le V-Sync (PIO bloc 0, SM 1)
#   sm0 (SM4) : front_montant    → déphasage H front montant (PIO bloc 1, SM 0)
#   sm1 (SM5) : front_descendant → déphasage H front descendant (PIO bloc 1, SM 1)
#   sm2 (SM6) : front_montant    → déphasage V front montant (PIO bloc 1, SM 2)
#   sm3 (SM7) : front_descendant → déphasage V front descendant (PIO bloc 1, SM 3)
# =============================================================================


# =============================================================================
# IMPORTATION DES MODULES
# =============================================================================

from machine import Pin, ADC
# 'Pin'  : permet de contrôler les broches GPIO du RP2040 (entrée/sortie).
# 'ADC'  : permet de lire une tension analogique sur une broche (0 à 3,3V)
#          et de la convertir en valeur numérique (0 à 65535).
#          Sur RP2040, les entrées ADC sont GP26, GP27, GP28.

import rp2
from rp2 import PIO, StateMachine, asm_pio
# 'rp2'         : module spécifique au RP2040 pour accéder aux PIO.
# 'PIO'         : accès aux constantes et types liés aux blocs PIO.
# 'StateMachine': classe pour créer et piloter une machine d'état PIO.
# 'asm_pio'     : décorateur (@) qui indique que la fonction suivante
#                 est un programme en assembleur PIO, pas du Python classique.

import time
# Module standard pour gérer les délais (sleep_ms, ticks_ms, etc.)

import micropython
micropython.stack_use()  # Retourne la taille de pile utilisée (lecture seule, pas d'effet ici)
# 'micropython' : module donnant accès à des fonctions bas niveau de MicroPython.
# stack_use()   : indique combien d'octets de pile sont utilisés. Utile pour
#                 détecter les débordements de pile lors du débogage.

import gc
# 'gc' = Garbage Collector (ramasse-miettes).
# En MicroPython, la mémoire est limitée (~200KB de RAM sur RP2040).
# Le GC libère automatiquement les objets Python qui ne sont plus utilisés.

gc.threshold(4096)
# Déclenche le GC automatiquement dès que 4096 octets ont été alloués
# depuis le dernier passage. Plus agressif que la valeur par défaut,
# ce qui évite les pics de consommation mémoire.

gc.collect()
# Force un passage immédiat du ramasse-miettes pour libérer la mémoire
# avant de démarrer le reste du programme.

micropython.mem_info()
# Affiche dans la console des informations détaillées sur la mémoire :
# heap (tas), stack (pile), blocs libres/utilisés.
# Utile pour le débogage mémoire. Ne fait rien visuellement sur l'écran.


# =============================================================================
# DÉCLARATION DES BROCHES (PINS)
# =============================================================================

# --- Entrées analogiques (potentiomètres) ---
Pot_Horizontal = ADC(26)
# Crée un objet ADC sur la broche GP26.
# Le potentiomètre horizontal est branché entre GND, GP26 et 3,3V.
# La valeur lue va de 0 (GND) à 65535 (3,3V).

Pot_Vertical = ADC(27)
# Idem pour le potentiomètre vertical sur GP27.

# --- Entrée numérique (bouton) ---
bouton_memoire = Pin(8, Pin.IN, Pin.PULL_UP)
# GP8 configurée en entrée avec résistance de tirage interne vers le HAUT (pull-up).
# Cela signifie que la broche lit 1 (HIGH) quand le bouton est relâché,
# et 0 (LOW) quand le bouton est appuyé (car il relie la broche à GND).
# C'est le câblage le plus courant et le plus sûr pour un bouton.

# --- Entrée numérique (signal C-Sync) ---
C_Sync_in = Pin(0, Pin.IN, Pin.PULL_DOWN)
# GP0 configurée en entrée avec pull-down (tirage vers le bas).
# Le signal composite de synchronisation y arrive depuis la source vidéo.
# Pull-down → état de repos = 0 (évite les flottements quand aucun signal).

# --- Sorties numériques ---
H_Sync_extrac = Pin(1, Pin.OUT)
# GP1 : sortie du signal H-Sync (synchronisation horizontale) extrait du C-Sync.

V_Sync_extrac = Pin(2, Pin.OUT)
# GP2 : sortie du signal V-Sync (synchronisation verticale) extrait du C-Sync.

H_Sync_phase_shifted = Pin(3, Pin.OUT)
# GP3 : H-Sync avec déphasage appliqué (décalé dans le temps).

V_Sync_phase_shifted = Pin(4, Pin.OUT)
# GP4 : V-Sync avec déphasage appliqué.

C_Sync_rebuild = Pin(5, Pin.OUT)
# GP5 : signal C-Sync reconstruit à partir de H-Sync et V-Sync déphasés.
#        (dans ce code, la reconstruction est faite par un circuit externe 74HC08)


# =============================================================================
# VARIABLES GLOBALES
# =============================================================================

dernier_appui = 0
# Mémorise l'horodatage (en millisecondes) du dernier appui valide sur le bouton.
# Initialisé à 0 (aucun appui encore détecté).

DEBOUNCE_MS = 300
# Durée minimale (en ms) entre deux appuis reconnus comme valides.
# Le "debounce" (anti-rebond) évite de détecter plusieurs appuis
# à cause des rebonds mécaniques du bouton (~300ms est une valeur classique).

mode_reglage = False
# Booléen qui indique si on est en mode réglage (True) ou en mode validé (False).
# Au démarrage, on est en mode validé : la valeur mémorisée est utilisée.


# =============================================================================
# FICHIER DE CONFIGURATION (SAUVEGARDE EN MÉMOIRE FLASH)
# =============================================================================

FICHIER_CONFIG = "dephasage_horiz_config.json"
# Nom du fichier texte dans lequel le déphasage horizontal est sauvegardé.
# Sur MicroPython, les fichiers sont stockés dans la mémoire flash du RP2040
# (système de fichiers FAT intégré). Le suffixe ".json" est ici conventionnel
# mais le fichier contient simplement un entier en texte brut.


# =============================================================================
# FONCTION : Chargement du déphasage sauvegardé
# =============================================================================

def charger_dephasage_horiz():
    """
    Lit le fichier de configuration et retourne la valeur de déphasage
    horizontal sauvegardée lors du dernier appui bouton.

    Si le fichier n'existe pas (premier démarrage) ou est illisible,
    retourne la valeur par défaut : 100 cycles.

    Retourne : un entier représentant le nombre de cycles de déphasage.
    """
    try:
        # Ouvre le fichier en lecture ('r' = read)
        with open(FICHIER_CONFIG, 'r') as f:
            val = int(f.read())  # Lit le contenu (ex: "4200") et le convertit en entier
            print("Config chargee : " + str(val) + " cycles")
            return val
    except:
        # Si une erreur survient (fichier absent, valeur corrompue, etc.)
        # on attrape l'exception et on retourne une valeur par défaut sûre.
        print("Pas de configuration sauvegardée, valeur par défaut : 100 cycles")
        return 100


# Appel immédiat au démarrage pour récupérer la dernière valeur mémorisée.
dephasage_horiz_memorise = charger_dephasage_horiz()
# Cette variable globale contient le déphasage horizontal à utiliser.
# Elle est mise à jour à chaque validation via le bouton.


# =============================================================================
# FONCTION : Sauvegarde du déphasage
# =============================================================================

def sauvegarder_dephasage_horiz(dephasage_horiz):
    """
    Écrit la valeur de déphasage horizontal dans le fichier de configuration.

    Paramètre : dephasage_horiz (int) → nombre de cycles à sauvegarder.
    Retourne   : True si la sauvegarde a réussi, False sinon.
    """
    try:
        # Ouvre le fichier en écriture ('w' = write, écrase le contenu précédent)
        with open(FICHIER_CONFIG, 'w') as f:
            f.write(str(dephasage_horiz))  # Écrit l'entier converti en texte
        print("Dephasage sauvegarde : " + str(dephasage_horiz) + " cycles")
        return True
    except Exception as e:
        # En cas d'erreur (mémoire flash pleine, problème d'écriture…),
        # affiche le message d'erreur mais ne fait pas planter le programme.
        print("Erreur de sauvegarde : " + str(e))
        return False


# =============================================================================
# FONCTION : Détection d'appui bouton avec anti-rebond (debounce)
# =============================================================================

def bouton_memoire_appuye():
    """
    Détecte un appui valide sur le bouton mémoire, avec gestion de l'anti-rebond.

    Principe :
    - Le bouton est actif à 0 (LOW) grâce au pull-up.
    - On vérifie qu'un délai suffisant s'est écoulé depuis le dernier appui.
    - On attend ensuite que le bouton soit relâché avant de retourner True.

    Retourne : True si un appui valide est détecté, False sinon.
    """
    global dernier_appui  # Indique qu'on modifie la variable globale (pas une variable locale)

    if bouton_memoire.value() == 0:  # 0 = bouton appuyé (actif bas avec pull-up)
        temps_actuel = time.ticks_ms()
        # ticks_ms() retourne le temps écoulé depuis le démarrage, en millisecondes.
        # C'est un compteur interne du RP2040 (peut déborder, d'où ticks_diff ci-dessous).

        if time.ticks_diff(temps_actuel, dernier_appui) > DEBOUNCE_MS:
            # ticks_diff() calcule correctement la différence même en cas de débordement
            # du compteur (qui repart à 0 après ~12 jours). C'est plus sûr que la soustraction directe.

            dernier_appui = temps_actuel  # Mémorise cet appui

            # Attend activement que le bouton soit relâché pour éviter de détecter
            # plusieurs appuis consécutifs tant que le doigt reste appuyé.
            while bouton_memoire.value() == 0:
                time.sleep_ms(10)  # Pause de 10ms pour ne pas saturer le CPU

            return True  # Appui valide confirmé

    return False  # Pas d'appui, ou trop tôt depuis le dernier


# =============================================================================
# MACHINE D'ÉTAT PIO N°1 : hsync_flywheel (extraction du H-Sync)
# =============================================================================
#
# RÔLE : Génère un signal H-Sync propre et régulier à partir du C-Sync entrant.
#        Fonctionne comme un "volant d'inertie" (flywheel) : il maintient la
#        fréquence horizontale même si le signal d'entrée est bruité.
#
# FONCTIONNEMENT :
#   1. Attend le front descendant du C-Sync (début de l'impulsion sync)
#   2. Génère une impulsion basse de ~5µs (625 cycles à 125MHz)
#   3. Reste haut pendant la durée d'une ligne (~59µs, soit ~7375 cycles)
#   4. Se resynchronise sur le prochain front descendant du C-Sync
#   → On obtient un H-Sync carré régulier sur la broche de sortie.
#
# TIMING :
#   À 125MHz, 1 cycle PIO = 8ns.
#   L'impulsion basse : 31 × (1 + 18 + 1 retour) ≈ 625 cycles ≈ 5µs
#   La période haute  : 7371 cycles chargés via put() ≈ 59µs
#
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH)
# @rp2.asm_pio : ce décorateur indique que la fonction est un programme PIO.
# set_init=PIO.OUT_HIGH : la broche SET démarre à l'état HAUT (1).
def hsync_flywheel():

    pull()
    # Lit une valeur depuis la FIFO TX (envoyée par sm4.put()) et la place dans OSR.
    # OSR = Output Shift Register, un registre de travail du PIO.
    # Ici, on charge la valeur 7371 (durée de la période haute en cycles).

    mov(x, osr)
    # Copie OSR dans le registre X.
    # X et Y sont les deux registres 32 bits de travail du PIO.
    # On s'en sert comme compteurs de boucle pour les délais.

    wrap_target()
    # Marque le début de la boucle principale.
    # Le PIO y reviendra automatiquement après wrap() en fin de programme.

    # --- IMPULSION SYNC BASSE (~5µs = 625 cycles) ---
    set(pins, 0)
    # Met la broche SET (H_Sync_extrac, GP1) à l'état BAS : début de l'impulsion sync.

    set(x, 30)
    # Charge 30 dans X. On va faire une boucle de 31 itérations (30 → 0).

    nop() [2]
    # NOP = No Operation (ne fait rien pendant 1 cycle).
    # [2] = délai supplémentaire de 2 cycles → total : 3 cycles consommés ici.

    label("sync_low")
    # Définit un label (point de saut) nommé "sync_low".

    nop() [18]
    # Attend 19 cycles (1 nop + 18 de délai).

    jmp(x_dec, "sync_low")
    # Décrémente X et saute à "sync_low" si X ≠ 0.
    # Boucle de 31 × 20 cycles ≈ 620 cycles + overhead ≈ 625 cycles ≈ 5µs.

    # --- RETOUR À L'ÉTAT HAUT ---
    set(pins, 1)
    # Met la broche H_Sync_extrac à l'état HAUT : fin de l'impulsion sync.

    # --- TEMPO BACK PORCH (durée d'une ligne moins l'impulsion) ---
    mov(x, osr)
    # Recharge X depuis OSR (valeur 7371) pour la boucle de délai.

    label("loop")
    jmp(x_dec, "loop")
    # Boucle simple de 7371 × 1 cycle = 7371 cycles ≈ 59µs.
    # Cette durée correspond approximativement à la période d'une ligne vidéo.

    # --- RESYNCHRONISATION SUR LE C-SYNC ---
    wait(0, pin, 0)
    # Attend que la broche d'entrée (in_base = C_Sync_in, GP0) passe à 0.
    # "pin, 0" = surveille la pin d'index 0 parmi les in_base.
    # Cela permet de se caler sur le prochain front descendant réel du C-Sync,
    # corrigeant les petites dérives de fréquence au fil du temps.

    wrap()
    # Retourne au wrap_target(). Recommence pour la ligne suivante.


# --- Instanciation de la machine d'état sm4 ---
sm4 = rp2.StateMachine(0, hsync_flywheel, freq=125_000_000,
                       set_base=Pin(H_Sync_extrac),  # Broche de sortie (set pins → GP1)
                       in_base=Pin(C_Sync_in))        # Broche d'entrée (wait pin → GP0)
# StateMachine(0, ...) : utilise la machine d'état n°0 du PIO bloc 0.
# freq=125_000_000 : horloge PIO à 125MHz → 1 cycle = 8 nanosecondes.
# set_base : toutes les instructions "set(pins,...)" agissent à partir de cette broche.
# in_base  : toutes les instructions "wait(pin,...)" lisent à partir de cette broche.

sm4.put(7371)
# Envoie 7371 dans la FIFO TX de sm4.
# Ce chiffre sera lu par pull() au démarrage du programme PIO.
# 7375 cycles théoriques - 4 cycles de traitement interne = 7371 cycles nets.

sm4.active(1)
# Démarre la machine d'état. 1 = activer, 0 = arrêter.


# =============================================================================
# MACHINE D'ÉTAT PIO N°2 : vsync_pio (extraction du V-Sync)
# =============================================================================
#
# RÔLE : Détecte les impulsions de synchronisation verticale dans le C-Sync.
#        En vidéo analogique, le V-Sync se distingue du H-Sync par une impulsion
#        PLUS LONGUE que les impulsions normales de synchronisation horizontale.
#
# PRINCIPE :
#   - Chaque front descendant du C-Sync est mesuré (durée à l'état bas).
#   - Si la durée dépasse le seuil (400 cycles ≈ 3,2µs), c'est un V-Sync.
#   - Sinon, c'est un H-Sync normal → on l'ignore.
#
# TIMING DU SEUIL :
#   400 cycles × 8ns = 3,2µs
#   H-Sync typique  : ~4,7µs → ATTENTION : le seuil détecte ici le "trou" V-Sync
#   V-Sync typique  : >10µs (impulsion beaucoup plus longue)
#
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH)
# La broche SET (V_Sync_extrac, GP2) démarre à l'état HAUT (pas de V-Sync).
def vsync_pio():

    pull()
    # Lit la valeur du seuil (400) depuis la FIFO TX.

    mov(y, osr)
    # Stocke le seuil dans Y. Y sera utilisé comme valeur de référence
    # (on ne le modifie pas dans la boucle principale, contrairement à X).

    wrap_target()

    wait(0, pin, 0)
    # Attend le front DESCENDANT du C-Sync (passage de 1 à 0).
    # Début de la mesure de la durée de l'impulsion basse.

    mov(x, y)
    # Charge le seuil (400) dans X. X servira de compteur descendant.

    label("measure")
    # La boucle de mesure prend exactement 2 cycles par itération.

    jmp(pin, "short")
    # "jmp(pin, ...)" : saute si la broche jmp_pin (C_Sync_in, GP0) est à 1.
    # Si le C-Sync est déjà remonté (fin de l'impulsion basse) → c'était court → H-Sync.

    jmp(x_dec, "measure")
    # Décrémente X et reboucle si X > 0.
    # Si X atteint 0 sans que le C-Sync soit remonté → impulsion longue → V-Sync !

    # === CAS V-SYNC DÉTECTÉ (X a atteint 0) ===

    set(pins, 0)
    # Met V_Sync_extrac (GP2) à l'état BAS : début du V-Sync en sortie.

    wait(1, pin, 0)
    # Attend que le C-Sync remonte (fin de l'impulsion de V-Sync entrante).

    mov(x, y)
    # Recharge X avec le seuil pour la temporisation (on réutilise Y comme durée).

    label("delay")
    jmp(x_dec, "delay") [1]
    # Boucle de délai. Le modificateur [1] ajoute 1 cycle supplémentaire par itération,
    # ce qui double la durée effective par rapport à une boucle simple (1 cycle/iter).
    # Cela compense la différence de timing entre cette boucle et la boucle de mesure
    # (qui prenait 2 cycles/iter) pour produire une sortie V-Sync symétrique.

    set(pins, 1)
    # Remet V_Sync_extrac à l'état HAUT : fin du V-Sync en sortie.

    wrap()
    # Retour au wrap_target() pour la prochaine détection.

    # === CAS H-SYNC NORMAL (impulsion courte) ===
    label("short")
    # On arrive ici quand jmp(pin,"short") a sauté (C-Sync déjà remonté).

    wait(1, pin, 0)
    # Attend le front MONTANT du C-Sync (fin de l'impulsion basse).
    # Permet de se re-synchroniser proprement avant le prochain front descendant.

    wrap()
    # Retour au wrap_target().


# --- Instanciation de la machine d'état sm5 ---
sm5 = rp2.StateMachine(1, vsync_pio, freq=125_000_000,
                       set_base=Pin(V_Sync_extrac),   # Sortie V-Sync → GP2
                       in_base=Pin(C_Sync_in),         # Entrée pour wait() → GP0
                       jmp_pin=Pin(C_Sync_in))          # Entrée pour jmp(pin,...) → GP0
# Notez que in_base et jmp_pin pointent toutes les deux vers C_Sync_in.
# Ce n'est pas redondant : "wait(pin,...)" utilise in_base, "jmp(pin,...)" utilise jmp_pin.
# Ces deux mécanismes sont indépendants dans le PIO du RP2040.

sm5.put(400)
# Envoie le seuil de détection : 400 cycles = 3,2µs.
# Une impulsion C-Sync plus longue que 3,2µs sera considérée comme un V-Sync.

sm5.active(1)
# Démarre sm5.


# =============================================================================
# MACHINE D'ÉTAT PIO N°3 : front_montant (déphasage sur front montant)
# =============================================================================
#
# RÔLE : Reproduit le signal d'entrée en décalant le front MONTANT d'un certain
#        nombre de cycles (le déphasage). Utilisé pour déplacer l'image.
#
# PRINCIPE :
#   1. Attend le front montant du signal d'entrée.
#   2. Attend X cycles (le déphasage chargé depuis la FIFO).
#   3. Met la sortie à 1 (front montant déphasé).
#   4. Attend le front descendant.
#   5. Retour en 1.
#
# Cette machine est utilisée deux fois :
#   - sm0 : pour le H-Sync (entrée GP1, sortie GP3)
#   - sm2 : pour le V-Sync (entrée GP2, sortie GP4)
#
@asm_pio(sideset_init=PIO.OUT_LOW)
# sideset_init=PIO.OUT_LOW : la broche sideset démarre à l'état BAS.
# "sideset" est un mécanisme PIO permettant de contrôler une broche
# SIMULTANÉMENT à une autre instruction (ex: nop().side(1) fait les deux en 1 cycle).
def front_montant():

    pull()
    # Lit le déphasage initial depuis la FIFO TX.

    mov(y, osr)
    # Stocke le déphasage dans Y (valeur de référence, rechargée à chaque cycle).

    wrap_target()

    wait(1, pin, 0)
    # Attend le front MONTANT du signal d'entrée (0 → 1).
    # C'est le moment de référence à partir duquel on calcule le déphasage.

    mov(x, y)
    # Copie Y dans X pour le compteur de délai.

    pull(noblock)
    # Essaie de lire une nouvelle valeur de déphasage depuis la FIFO TX.
    # "noblock" = non bloquant : si la FIFO est vide, OSR reste inchangé.
    # Cela permet de mettre à jour le déphasage en temps réel depuis le Python principal.

    mov(y, osr)
    # Met à jour Y avec la nouvelle valeur (ou la précédente si FIFO vide).

    label("delay_high")
    jmp(x_dec, "delay_high")
    # Boucle de X cycles = délai de déphasage.
    # Plus X est grand, plus le front montant sera retardé.

    nop() .side(1)
    # Attend 1 cycle ET met simultanément la broche sideset à 1 (front montant déphasé).
    # La notation ".side(1)" est propre à l'assembleur PIO.

    wait(0, pin, 0)
    # Attend le front DESCENDANT du signal d'entrée.
    # (Le programme ne gère pas le front descendant déphasé ici → voir front_descendant.)

    wrap()
    # Recommence pour la prochaine impulsion.


# --- Instanciation sm0 : déphasage H-Sync front montant ---
sm0 = StateMachine(4, front_montant, freq=125_000_000,
                   in_base=H_Sync_extrac,          # Entrée : H-Sync extrait (GP1)
                   sideset_base=H_Sync_phase_shifted)  # Sortie : H-Sync déphasé (GP3)
# StateMachine(4,...) : machine d'état n°4, dans le PIO bloc 1 (SM 0 du bloc 1).
# Les 4 premières SM (0-3) sont dans le PIO bloc 0, les 4 suivantes (4-7) dans le bloc 1.

sm0.put(dephasage_horiz_memorise)
# Envoie le déphasage horizontal mémorisé au démarrage.

sm0.active(1)

# --- Instanciation sm2 : déphasage V-Sync front montant ---
sm2 = StateMachine(6, front_montant, freq=125_000_000,
                   in_base=V_Sync_extrac,            # Entrée : V-Sync extrait (GP2)
                   sideset_base=V_Sync_phase_shifted) # Sortie : V-Sync déphasé (GP4)
# StateMachine(6,...) : machine d'état n°6, dans le PIO bloc 1 (SM 2 du bloc 1).

sm2.put(100)
# Déphasage vertical initial fixé à 100 cycles (non réglable dans cette version,
# le potentiomètre vertical est câblé mais son réglage est expérimental).

sm2.active(1)


# =============================================================================
# MACHINE D'ÉTAT PIO N°4 : front_descendant (déphasage sur front descendant)
# =============================================================================
#
# RÔLE : Complémentaire à front_montant. Reproduit le signal en décalant le
#        front DESCENDANT. Utilisé en tandem avec front_montant pour reconstituer
#        un signal complet avec les deux fronts déphasés identiquement.
#
# DIFFÉRENCE avec front_montant :
#   - On attend d'abord le front montant (pour se synchroniser),
#   - Puis le front descendant (qui est l'événement qu'on veut déphaser),
#   - Ensuite on attend X cycles,
#   - Puis on met la sortie à 0 (front descendant déphasé).
#
@asm_pio(sideset_init=PIO.OUT_LOW)
def front_descendant():

    pull()
    mov(y, osr)
    # Même logique que front_montant : charge le déphasage depuis la FIFO dans Y.

    wrap_target()

    wait(1, pin, 0)
    # Attend le front MONTANT du signal d'entrée (pour se synchroniser sur le signal).

    wait(0, pin, 0)
    # Attend immédiatement le front DESCENDANT suivant.
    # C'est l'instant de référence pour le déphasage du front bas.

    mov(x, y)
    # Copie le déphasage dans X pour la boucle de délai.

    pull(noblock)
    mov(y, osr)
    # Met à jour le déphasage en temps réel (même mécanisme que front_montant).

    label("delay_low")
    jmp(x_dec, "delay_low")
    # Boucle de délai : X cycles de déphasage.

    nop() .side(0)
    # Attend 1 cycle ET met la broche sideset à 0 (front descendant déphasé).

    wait(1, pin, 0)
    # Attend le prochain front MONTANT (fin de l'impulsion basse).

    wrap()


# --- Instanciation sm1 : déphasage H-Sync front descendant ---
sm1 = StateMachine(5, front_descendant, freq=125_000_000,
                   in_base=H_Sync_extrac,
                   sideset_base=H_Sync_phase_shifted)
# NOTE : sm0 et sm1 utilisent la même broche de sortie (H_Sync_phase_shifted, GP3).
# sm0 gère les fronts montants, sm1 gère les fronts descendants.
# Les deux machines travaillent en parallèle sur la même broche de sortie.

sm1.put(dephasage_horiz_memorise)
sm1.active(1)

# --- Instanciation sm3 : déphasage V-Sync front descendant ---
sm3 = StateMachine(7, front_descendant, freq=125_000_000,
                   in_base=V_Sync_extrac,
                   sideset_base=V_Sync_phase_shifted)
# Idem : sm2 et sm3 partagent la broche V_Sync_phase_shifted (GP4).

sm3.put(100)
sm3.active(1)


# =============================================================================
# MACHINE D'ÉTAT PIO N°5 (DÉSACTIVÉE) : porte logique ET
# =============================================================================
#
# Cette machine d'état devait reconstituer le C-Sync en faisant un ET logique
# entre H-Sync déphasé et V-Sync déphasé (C-Sync = H-Sync AND V-Sync).
#
# Elle a été abandonnée pour deux raisons :
#   1. Le PIO bloc 0 est déjà plein (4 SM sur 4 utilisées : sm4 et sm5 + 2 slots).
#   2. Le timing de sortie était incorrect (le PIO introduisait un retard variable).
#
# SOLUTION RETENUE : utiliser un circuit logique externe 74HC08 (porte ET en boîtier)
# branché entre GP3, GP4 et GP5. Plus simple et timing parfait.
#
# Le code commenté est conservé à titre de documentation.

# @rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, autopush=False, autopull=False)
# def and_gate():
#     wrap_target()
#     in_(pins, 2)          # lit les 2 broches à partir de in_base (GP6 et GP7)
#     mov(x, isr)           # copie les 2 bits lus dans X
#     mov(isr, null)        # remet ISR à zéro pour la prochaine lecture
#     set(y, 3)             # Y = 0b11 (les deux bits à 1 = les deux entrées hautes)
#     jmp(x_not_y, "low")  # si X ≠ 3 (au moins une entrée basse) → sortie basse
#     set(pins, 1)          # sinon → sortie haute (ET logique = 1)
#     jmp("done")
#     label("low")
#     set(pins, 0)          # sortie basse
#     label("done")
#     nop() [31]            # pause de 32 cycles avant de reboucler
#     wrap()

# sm6 = rp2.StateMachine(2, and_gate, freq=125_000_000,
#                        in_base=Pin(H_Sync_phase_shifted),
#                        set_base=Pin(C_Sync_rebuild))
# sm6.active(1)


# =============================================================================
# INITIALISATION FINALE AVANT LA BOUCLE PRINCIPALE
# =============================================================================

gc.collect()
# Dernier passage du ramasse-miettes avant d'entrer dans la boucle infinie.
# Libère toute mémoire temporaire utilisée pendant l'initialisation.

print("\n✓ Système démarré avec le déphasage sauvegardé")
print("Appuyez sur le bouton_memoire pour modifier le réglage\n")


# =============================================================================
# BOUCLE PRINCIPALE (tourne indéfiniment)
# =============================================================================
#
# LOGIQUE DE FONCTIONNEMENT :
#
#   [Mode validé] ←────────────── [Appui bouton] ──────────────→ [Mode réglage]
#       │                                                              │
#       │  Envoie dephasage_horiz_memorise aux SM0 et SM1             │  Lit les potentiomètres en temps réel
#       │  Pause de 500ms                                             │  Envoie les valeurs aux SM
#       └──────────────────────────────────────────────────────────────┘
#
while True:

    # -------------------------------------------------------------------------
    # DÉTECTION D'APPUI SUR LE BOUTON MÉMOIRE
    # -------------------------------------------------------------------------
    if bouton_memoire_appuye():
        # Un appui valide a été détecté (avec debounce).

        if mode_reglage:
            # ─────────────────────────────────────────
            # ON ÉTAIT EN MODE RÉGLAGE → on VALIDE
            # ─────────────────────────────────────────
            val = Pot_Horizontal.read_u16()
            # Lit la position actuelle du potentiomètre horizontal.
            # Valeur brute entre 0 et 65535.

            dephasage_horiz_memorise = (val * 8000) // 65535
            # Conversion linéaire : on mappe [0..65535] → [0..8000] cycles.
            # 8000 cycles à 125MHz = 64µs (légèrement plus d'une ligne vidéo).
            # "// 65535" = division entière (résultat = entier, pas de décimale).

            mode_reglage = False  # Retour en mode validé.

            if sauvegarder_dephasage_horiz(dephasage_horiz_memorise):
                print(f"✓ DÉPHASAGE VALIDÉ ET SAUVEGARDÉ : {dephasage_horiz_memorise} cycles")
                print("Ce réglage sera rappelé au prochain démarrage")

            print("Appuyez à nouveau pour modifier\n")

        else:
            # ─────────────────────────────────────────
            # ON ÉTAIT EN MODE VALIDÉ → on PASSE en mode réglage
            # ─────────────────────────────────────────
            mode_reglage = True
            print("\n=== MODE RÉGLAGE ===")
            print("Tournez le potentiomètre, puis appuyez pour valider et sauvegarder")

    # -------------------------------------------------------------------------
    # GESTION DU DÉPHASAGE EN TEMPS RÉEL (mode réglage)
    # -------------------------------------------------------------------------
    if mode_reglage:

        # Lecture du potentiomètre horizontal
        val_horiz = Pot_Horizontal.read_u16()
        # Valeur ADC brute : 0 = potentiomètre tourné à fond vers la gauche,
        #                65535 = tourné à fond vers la droite.

        dephasage_horiz = (val_horiz * 8000) // 65535
        # Conversion en cycles de déphasage horizontal (0 à 8000 cycles).

        # Lecture du potentiomètre vertical
        val_verti = Pot_Vertical.read_u16()
        dephasage_verti = (val_verti * 2115000) // 65535
        # Conversion en cycles de déphasage vertical (0 à 2 115 000 cycles).
        # La valeur max est beaucoup plus grande car une trame vidéo (champ vertical)
        # est ~312 lignes × ~7400 cycles/ligne ≈ 2 100 000 cycles.
        #
        # NOTE : Le réglage vertical est expérimental dans cette version.
        #        Un commentaire dans le code original indique que des tests
        #        avec une autre fréquence d'horloge seraient nécessaires.

        # Envoi du déphasage horizontal aux machines d'état sm0 et sm1
        if sm0.tx_fifo() < 4:
            sm0.put(dephasage_horiz)
        # tx_fifo() retourne le nombre de valeurs actuellement dans la FIFO TX.
        # La FIFO a une profondeur de 4. On n'envoie que si elle n'est pas pleine
        # pour ne pas bloquer (put() est bloquant si la FIFO est pleine).
        # Ainsi, les machines d'état reçoivent la valeur mise à jour le plus vite possible.

        if sm1.tx_fifo() < 4:
            sm1.put(dephasage_horiz)
        # sm0 et sm1 reçoivent le même déphasage (fronts montant et descendant identiques).

        # Envoi du déphasage vertical aux machines d'état sm2 et sm3
        if sm2.tx_fifo() < 4:
            sm2.put(dephasage_verti)
        if sm3.tx_fifo() < 4:
            sm3.put(dephasage_verti)

        # Affichage en temps réel dans la console (mis à jour toutes les 100ms)
        print(f"RÉGLAGE - Cycles: {dephasage_horiz} | pot: {val_horiz} | "
              f"RÉGLAGE - Cycles: {dephasage_verti} | pot: {val_verti}      ", end='\r')
        # end='\r' : le curseur revient au début de la ligne (carriage return)
        # sans sauter à la ligne → l'affichage se met à jour sur place, proprement.

        time.sleep_ms(100)
        # Pause de 100ms entre chaque rafraîchissement.
        # Suffisant pour un réglage fluide au potentiomètre, sans saturer le CPU.

    else:
        # ─────────────────────────────────────────
        # MODE VALIDÉ : on envoie la valeur mémorisée en continu
        # ─────────────────────────────────────────
        # Les machines d'état pull(noblock) dans leur boucle interne :
        # elles essaient de lire la FIFO à chaque cycle, mais sans bloquer.
        # On doit donc alimenter régulièrement la FIFO pour que la valeur
        # de déphasage reste à jour (sinon elles utilisent l'ancienne valeur OSR).

        if sm0.tx_fifo() < 4:
            sm0.put(dephasage_horiz_memorise)
        if sm1.tx_fifo() < 4:
            sm1.put(dephasage_horiz_memorise)
        # Note : sm2 et sm3 (V-Sync) ne sont pas ré-alimentées ici car le
        # déphasage vertical n'est pas réglable en mode validé dans cette version.

        time.sleep_ms(500)
        # Pause plus longue en mode validé : rien à afficher, on économise le CPU.
        # 500ms est amplement suffisant pour maintenir la FIFO alimentée.
