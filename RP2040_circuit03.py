from machine import Pin, ADC
from rp2 import PIO, StateMachine, asm_pio
import time
import gc
import micropython

gc.collect()  # Libère la mémoire
micropython.mem_info()
print(f"Mémoire libre : {gc.mem_free()} bytes")

Pot_Horizontal = ADC(26)
Pot_Vertical = ADC(27)
bouton_memoire = Pin(8, Pin.IN, Pin.PULL_UP)  # bouton_memoire sur GP8 (actif à 0, pull-up interne)

# Fichier de sauvegarde
FICHIER_CONFIG = "dephasage_horiz_config.json"

# Variables pour le débounce
dernier_appui = 0
DEBOUNCE_MS = 300  # Temps minimum entre deux appuis (en ms)

# Fonction pour charger le déphasage sauvegardé
def charger_dephasage_horiz():
    try:
        with open(FICHIER_CONFIG, 'r') as f:
            val = int(f.read())
            print(f"✓ Configuration chargée : {config['dephasage_horiz']} cycles")
            return val
    except:
        print("⚠ Pas de configuration sauvegardée, valeur par défaut : 100 cycles")
        return 100  # Valeur par défaut

# Fonction pour sauvegarder le déphasage
def sauvegarder_dephasage_horiz(dephasage_horiz):
    try:
        with open(FICHIER_CONFIG, 'w') as f:
            f.write(str(dephasage_horiz))
        print(f"💾 Déphasage sauvegardé : {dephasage_horiz} cycles")
        return True
    except Exception as e:
        print(f"❌ Erreur de sauvegarde : {e}")
        return False
    
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

# Horizontal, State Machine 0 : pour le front montant
@asm_pio(sideset_init=PIO.OUT_LOW)
def Horizontal_front_montant():
    pull()
    mov(y, osr)
    wrap_target()
    wait(1, pin, 0)
  
    mov(x, y)           # Charge le délai AVANT de tenter la mise à jour
    pull(noblock)       # Mise à jour pour le cycle SUIVANT
    mov(y, osr)
  
    label("Horiz_delay_high")
    jmp(x_dec, "Horiz_delay_high")
    
    nop()         .side(1)
    wait(0, pin, 0)
    wrap()

# Horizontal, SM1 pour le front descendant
@asm_pio(sideset_init=PIO.OUT_LOW)
def Horizontal_front_descendant():
    pull()
    mov(y, osr)
    wrap_target()
    
    wait(1, pin, 0)
    wait(0, pin, 0)
   
    mov(x, y)           # Charge le délai AVANT de tenter la mise à jour
    pull(noblock)       # Mise à jour pour le cycle SUIVANT
    mov(y, osr) 
   
    label("Horiz_delay_low")
    jmp(x_dec, "Horiz_delay_low")
    
    nop()         .side(0)
    wait(1, pin, 0)
    wrap()

# Vertical, State Machine 2 : pour le front montant
@asm_pio(sideset_init=PIO.OUT_LOW)
def Vertical_front_montant():
    pull()
    mov(y, osr)
    wrap_target()
    wait(1, pin, 0)
  
    mov(x, y)           # Charge le délai AVANT de tenter la mise à jour
    pull(noblock)       # Mise à jour pour le cycle SUIVANT
    mov(y, osr)
  
    label("Verti_delay_high")
    jmp(x_dec, "Verti_delay_high")
    
    nop()         .side(1)
    wait(0, pin, 0)
    wrap()

# Vertical, SM3 pour le front descendant
@asm_pio(sideset_init=PIO.OUT_LOW)
def Vertical_front_descendant():
    pull()
    mov(y, osr)
    wrap_target()
    
    wait(1, pin, 0)
    wait(0, pin, 0)
    
    mov(x, y)           # Charge le délai AVANT de tenter la mise à jour
    pull(noblock)       # Mise à jour pour le cycle SUIVANT
    mov(y, osr) 
   
    label("Verti_delay_low")
    jmp(x_dec, "Verti_delay_low")
    
    nop()         .side(0)
    wait(1, pin, 0)
    wrap()


# --- Horizontal, CONFIGURATION ---
horiz_pin_in = Pin(14, Pin.IN, Pin.PULL_DOWN)
horiz_pin_out = Pin(15, Pin.OUT)

sm0 = StateMachine(0, Horizontal_front_montant, freq=125_000_000, 
                   in_base=horiz_pin_in, sideset_base=horiz_pin_out)
sm1 = StateMachine(1, Horizontal_front_descendant, freq=125_000_000, 
                   in_base=horiz_pin_in, sideset_base=horiz_pin_out)

# CHARGEMENT DU DÉPHASAGE SAUVEGARDÉ
dephasage_horiz_memorise = charger_dephasage_horiz()

sm0.put(dephasage_horiz_memorise)
sm1.put(dephasage_horiz_memorise)

sm0.active(1)
sm1.active(1)


# --- Vertical, CONFIGURATION ---
# SM4 et SM5 sur PIO1 (Vertical) - NUMÉROS 4 et 5 !
verti_pin_in = Pin(5, Pin.IN, Pin.PULL_DOWN)
verti_pin_out = Pin(6, Pin.OUT)

sm2 = StateMachine(4, Vertical_front_montant, freq=125_000_000, 
                   in_base=verti_pin_in, sideset_base=verti_pin_out)
sm3 = StateMachine(5, Vertical_front_descendant, freq=125_000_000,
                    in_base=verti_pin_in, sideset_base=verti_pin_out)

sm2.put(100)
sm3.put(100)

sm2.active(1)
sm3.active(1)


# Variables pour gérer le bouton_memoire
mode_reglage = False  # On démarre en mode validé (avec la valeur chargée)

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
