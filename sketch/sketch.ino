// Pulsador físico -- debounce por software + aviso a Linux vía Bridge.
//
// D2 en INPUT_PULLUP: HIGH = suelto, LOW = presionado (confirmado por
// diagnóstico previo). Cada pulsación válida (una sola notificación por
// apretón, no una por rebote del contacto mecánico) dispara una captura del
// lado Python -- ver blindia/triggers/button.py.
//
// Debounce por conteo de muestras consecutivas, no por "tiempo de quietud".
// Una primera versión reiniciaba un temporizador cada vez que la lectura
// cruda cambiaba, y solo confirmaba el nuevo estado tras 50ms sin ningún
// cambio -- con un contacto ligeramente ruidoso, cada rebote reiniciaba ese
// temporizador indefinidamente y el estado nunca llegaba a confirmarse (el
// botón quedaba "trabado" hasta reflashear el sketch, que reinicializa las
// variables). Este enfoque en cambio cuenta muestras consecutivas hacia el
// nuevo estado; una muestra que vuelve a coincidir con el estado YA
// confirmado reinicia el contador (correcto: es ruido, no una transición
// real), pero nunca queda esperando una ventana de quietud que el ruido
// pueda romper para siempre.
#include <Arduino_RouterBridge.h>

const int PIN_BOTON = 2;
const unsigned long INTERVALO_MUESTREO_MS = 10;
const uint8_t MUESTRAS_PARA_CONFIRMAR = 4;  // 4 x 10ms = 40ms de lectura consistente

int estadoEstable = HIGH;
uint8_t contador = MUESTRAS_PARA_CONFIRMAR;
unsigned long ultimoMuestreo = 0;

void setup() {
  pinMode(PIN_BOTON, INPUT_PULLUP);
  Bridge.begin();
}

void loop() {
  unsigned long ahora = millis();
  if (ahora - ultimoMuestreo < INTERVALO_MUESTREO_MS) {
    return;
  }
  ultimoMuestreo = ahora;

  int lectura = digitalRead(PIN_BOTON);

  if (lectura == estadoEstable) {
    contador = MUESTRAS_PARA_CONFIRMAR;  // reafirma el estado actual, nada que confirmar
    return;
  }

  // Lectura distinta del último estado confirmado: cuenta muestras
  // consecutivas hacia el posible nuevo estado.
  contador--;
  if (contador == 0) {
    estadoEstable = lectura;
    contador = MUESTRAS_PARA_CONFIRMAR;
    if (estadoEstable == LOW) {  // flanco de bajada = pulsación válida
      Bridge.notify("boton_presionado");
    }
  }
}
