from machine import Pin, ADC
import rp2
from rp2 import PIO, StateMachine, asm_pio
import time
import micropython
micropython.stack_use()  # lecture seule
# Forcer une allocation minimale dès le début
import gc

gc.threshold(4096)  # GC plus agressif (déclenché dès 4KB d'allocs)
gc.collect()
micropython.mem_info()
#print(f"Mémoire libre : {gc.mem_free()} bytes")

###########################
# Déclaration des broches #
###########################
Pot_Horizontal = ADC(26)
Pot_Vertical = ADC(27)
bouton_memoire = Pin(8, Pin.IN, Pin.PULL_UP)  # bouton_memoire sur GP8 (actif à 0, pull-up interne)
C_Sync_in = Pin(0, Pin.IN, Pin.PULL_DOWN)
H_Sync_extrac = Pin(1, Pin.OUT)
V_Sync_extrac = Pin(2, Pin.OUT)
H_Sync_phase_shifted = Pin(3, Pin.OUT)
V_Sync_phase_shifted = Pin(4, Pin.OUT)
C_Sync_rebuild = Pin(5, Pin.OUT)

#############
# Variables #
#############
dernier_appui = 0
DEBOUNCE_MS = 300  # Temps minimum entre deux appuis (en ms)
mode_reglage = False  # pour gérer le bouton_memoire, on démarre en mode validé (avec la valeur chargée)

#########################
# Fichier de sauvegarde #
#########################
FICHIER_CONFIG = "dephasage_horiz_config.json"

#################################################
# Fonction pour charger le déphasage sauvegardé #
#################################################
def charger_dephasage_horiz():
    try:
        with open(FICHIER_CONFIG, 'r') as f:
            val = int(f.read())
            print("Config chargee : " + str(val) + " cycles")  # <-- BUG corrigé
            return val
    except:
        print("Pas de configuration sauvegardée, valeur par défaut : 100 cycles")
        return 100  # Valeur par défaut

# CHARGEMENT DU DÉPHASAGE SAUVEGARDÉ
dephasage_horiz_memorise = charger_dephasage_horiz()

##########################################
# Fonction pour sauvegarder le déphasage #
##########################################
def sauvegarder_dephasage_horiz(dephasage_horiz):
    try:
        with open(FICHIER_CONFIG, 'w') as f:
            f.write(str(dephasage_horiz))
        print("Dephasage sauvegarde : " + str(dephasage_horiz) + " cycles")
        return True
    except Exception as e:
        print("Erreur de sauvegarde : " + str(e))
        return False
 
###################################
# Fonction pour le bouton mémoire #
###################################
def bouton_memoire_appuye():
    """Détecte un appui avec débounce - retourne True si appui valide"""
    global dernier_appui
    if bouton_memoire.value() == 0:  # bouton_memoire enfoncé
        temps_actuel = time.ticks_ms()
        # Vérifie si assez de temps s'est écoulé depuis le dernier appui
        if time.ticks_diff(temps_actuel, dernier_appui) > DEBOUNCE_MS:
            dernier_appui = temps_actuel
            # Attendre que le bouton_memoire soit relâché
            while bouton_memoire.value() == 0:
                time.sleep_ms(10)
            return True
    return False

#################################################
# Fonction pour State machine : flywheel H−Sync #
#################################################
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH)
def hsync_flywheel():
    pull()
    mov(x, osr)
    wrap_target()
    # --- RESYNC : attend le front descendant, mais sans bloquer trop longtemps ---
 #   wait(0, pin, 0)    # attend le prochain front entrant
    # --- IMPULSION SYNC --- 625 cycle 5µs
    set(pins, 0)
    set(x, 30)
    nop() [2]
    label("sync_low")
    nop() [18]
    jmp(x_dec, "sync_low")
    # --- RETOUR HAUT ---
    set(pins, 1)
    # --- TEMPO BACK PORCH ---
    mov(x, osr)
    label("loop")
    jmp(x_dec, "loop")
    # --- RESYNC : attend le front descendant, mais sans bloquer trop longtemps ---
    wait(0, pin, 0)    # attend le prochain front entrant
# placé ici ou au début, même résultat
    wrap()
# Déclaration State machine
sm4 = rp2.StateMachine(0, hsync_flywheel, freq=125_000_000,
                       set_base=Pin(H_Sync_extrac), # sortie
                       in_base=Pin(C_Sync_in))   # ← pin d'entrée pour wait
sm4.put(7371) # 7375 cycle 59µs 7375 - 4 cycle process = 7371
sm4.active(1)

######################################################
# Fonction pour State machine : Extraction de V−Sync #
######################################################
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH)
def vsync_pio():
    pull()
    mov(y, osr)         # seuil 1250 (10µs)

    wrap_target()
    wait(0, pin, 0)     # front descendant C-sync

    mov(x, y)
    label("measure")
    jmp(pin, "short")
    jmp(x_dec, "measure")

    # Trou détecté → V-sync bas
    set(pins, 0)
    wait(1, pin, 0)     # attendre le premier front montant C-sync
    set(pins, 1)        # V-sync haut
    wrap()

    label("short")
    wait(1, pin, 0)
    wrap()
# Déclaration State machine
sm5 = rp2.StateMachine(1, vsync_pio, freq=125_000_000,
                       set_base=Pin(V_Sync_extrac),
                       in_base=Pin(C_Sync_in),
                       jmp_pin=Pin(C_Sync_in))
sm5.put(400)   # seuil 1250 (10µs)
sm5.active(1)    

###############################################
# Fonction pour State machine : front montant # 
###############################################
@asm_pio(sideset_init=PIO.OUT_LOW)
def front_montant():
    pull()
    mov(y, osr)
    wrap_target()
    wait(1, pin, 0)
    mov(x, y)
    pull(noblock)
    mov(y, osr)
    label("delay_high")
    jmp(x_dec, "delay_high")
    nop()         .side(1)
    wait(0, pin, 0)
    wrap()
# Déclaration State machine
sm0 = StateMachine(4, front_montant, freq=125_000_000,
                   in_base=H_Sync_extrac,
                   sideset_base=H_Sync_phase_shifted)
sm0.put(dephasage_horiz_memorise)
sm0.active(1)

# Déclaration State machine
sm2 = StateMachine(6, front_montant, freq=125_000_000,
                   in_base=V_Sync_extrac,
                   sideset_base=V_Sync_phase_shifted)
sm2.put(100)
sm2.active(1)

##################################################
# Fonction pour State machine : front descendant # 
##################################################
@asm_pio(sideset_init=PIO.OUT_LOW)
def front_descendant():
    pull()
    mov(y, osr)
    wrap_target()
    wait(1, pin, 0)
    wait(0, pin, 0)
    mov(x, y)
    pull(noblock)
    mov(y, osr)
    label("delay_low")
    jmp(x_dec, "delay_low")
    nop()         .side(0)
    wait(1, pin, 0)
    wrap()
# Déclaration State machine
sm1 = StateMachine(5, front_descendant, freq=125_000_000,
                   in_base=H_Sync_extrac,
                   sideset_base=H_Sync_phase_shifted)
sm1.put(dephasage_horiz_memorise)
sm1.active(1)

# Déclaration State machine
sm3 = StateMachine(7, front_descendant, freq=125_000_000,
                   in_base=V_Sync_extrac,
                   sideset_base=V_Sync_phase_shifted)
sm3.put(100)
sm3.active(1)

##################################################
# Fonction pour State machine : Porte logique ET #
##################################################
@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, autopush=False, autopull=False)
def and_gate():
    wrap_target()
    in_(pins, 2)          # lit pin 6 et 7
    mov(x, isr)           # X = état actuel
    mov(isr, null)
    set(y, 3)
    jmp(x_not_y, "low")
    set(pins, 1)
    jmp("done")
    label("low")
    set(pins, 0)
    label("done")
    nop() [31]            # attend 32 cycles avant de reboucler
    wrap()
# Déclaration State machine
sm6 = rp2.StateMachine(2, and_gate, freq=125_000_000,
                       in_base=Pin(H_Sync_phase_shifted),   # pins 6 et 7 lues en séquence
                       set_base=Pin(C_Sync_rebuild))  # pin de sortie
sm6.active(1)

#######################
# Programme principal #
#######################
gc.collect()
print("\n✓ Système démarré avec le déphasage sauvegardé")
print("Appuyez sur le bouton_memoire pour modifier le réglage\n")
while True:
    # --- DÉTECTION D'APPUI AVEC DÉBOUNCE ---
    if bouton_memoire_appuye():
        if mode_reglage:
            # VALIDATION
            val = Pot_Horizontal.read_u16()
            dephasage_horiz_memorise = (val * 8000) // 65535
            mode_reglage = False
            
            if sauvegarder_dephasage_horiz(dephasage_horiz_memorise):
                print(f"✓ DÉPHASAGE VALIDÉ ET SAUVEGARDÉ : {dephasage_horiz_memorise} cycles")
                print("Ce réglage sera rappelé au prochain démarrage")
            
            print("Appuyez à nouveau pour modifier\n")
        else:
            # RETOUR EN MODE RÉGLAGE
            mode_reglage = True
            print("\n=== MODE RÉGLAGE ===")
            print("Tournez le potentiomètre, puis appuyez pour valider et sauvegarder")
        
    # --- GESTION DU DÉPHASAGE ---
    if mode_reglage:
        # Mode réglage : lecture en temps réel du potentiomètre Horizontal
        val_horiz = Pot_Horizontal.read_u16()
        dephasage_horiz = (val_horiz * 8000) // 65535

        # Mode réglage : lecture en temps réel du potentiomètre Vertical
        val_verti = Pot_Vertical.read_u16()
        dephasage_verti = (val_verti * 2115000) // 65535
    # DÉPLACEMENT DE L'IMAGE COMPLIQUÉ… Tester avec autre fréquence d'horloge…
        
        # Envoi aux SM pour voir le réglage en temps réel Horizontal
        if sm0.tx_fifo() < 4:
            sm0.put(dephasage_horiz)
        if sm1.tx_fifo() < 4:
            sm1.put(dephasage_horiz)

        # Envoi aux SM pour voir le réglage en temps réel Vertical
        if sm2.tx_fifo() < 4:
            sm2.put(dephasage_verti)
        if sm3.tx_fifo() < 4:
            sm3.put(dephasage_verti)
        
        print(f"RÉGLAGE - Cycles: {dephasage_horiz} | pot: {val_horiz} | RÉGLAGE - Cycles: {dephasage_verti} | pot: {val_verti}      ", end='\r')
        
        time.sleep_ms(100)
    else:
        # Mode validé : on envoie la valeur mémorisée
        # On n'envoie que si la FIFO n'est pas déjà pleine pour éviter de spammer
        if sm0.tx_fifo() < 4:
            sm0.put(dephasage_horiz_memorise)
        if sm1.tx_fifo() < 4:
            sm1.put(dephasage_horiz_memorise)
        
        # Affichage moins fréquent en mode validé
        time.sleep_ms(500)
