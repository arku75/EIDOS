# EIDOS — Historia y evolución documentada

> **Documento vivo · edición 2026-10-04**
>
> Esta no es una página de marketing ni una afirmación de vida biológica o consciencia científica.
> Es la historia técnica y humana de EIDOS tal como puede reconstruirse a partir de TASK.md,
> documentación del proyecto, snapshots del grafo, dossiers, restauraciones, vídeos y artefactos
> visuales conservados por SER.
>
> **Regla de lectura:** que algo esté construido no significa que esté cableado; que esté cableado
> no significa que esté verificado; que funcione una vez no significa que sea una capacidad general.

---

## 1. Qué es EIDOS

EIDOS nació con una idea que fue creciendo más allá de un chatbot o de una interfaz alrededor de
un modelo de lenguaje. Su arquitectura intenta mantener una identidad computacional persistente:
estado, memoria, conocimiento, personajes, percepción, acción, errores, aprendizaje y continuidad
entre sesiones.

La forma más precisa de describirlo hoy es:

**EIDOS es una arquitectura local de agentes persistentes que intenta cerrar un ciclo completo entre
mundo, percepción, memoria, deliberación, decisión, acción, verificación y aprendizaje.**

Los modelos de lenguaje pueden formar parte de algunos caminos, pero no son el único lugar donde
residen el estado, la memoria o las reglas del sistema.

El ciclo perseguido por el proyecto es:

```text
MUNDO
  ↓
PERCEPCIÓN
  ↓
MEMORIA + GRAFO
  ↓
COLONIA / MECANISMOS DE CONSEJO
  ↓
EIDOS / ARBITRAJE
  ↓
ACCIÓN
  ↓
EFECTO REAL
  ↓
VERIFICACIÓN
  ↓
EXPERIENCIA
  ↓
APRENDIZAJE
  ↺
```

---

## 2. El principio que atraviesa todo el proyecto

Una frase reaparece en la documentación interna:

**USE → UNDERSTAND → REPLICATE → OWN**

Usar una capacidad externa no significa poseerla. El objetivo es observarla, entender su efecto,
reproducir el mecanismo y, cuando sea posible, convertirlo en una capacidad propia y verificable.

La segunda regla epistemológica es igual de importante:

**HEARD ≠ VERIFIED**

Un dato oído, generado o encontrado puede ser una pista. No debería transformarse automáticamente
en verdad interna. EIDOS ha ido añadiendo puertas de corroboración, medición, aislamiento,
negative controls, bancos held-out y verificación por efecto precisamente porque el proyecto ha
encontrado repetidamente casos donde una respuesta plausible no equivalía a un resultado real.

---

## 3. Mayo de 2026 — el nacimiento operativo

La historia documental sitúa el arranque operativo del proyecto el **24 de mayo de 2026**.

El punto de partida era pequeño comparado con lo que llegaría después: un grafo que comenzaba a
representar conocimiento, código y relaciones, acompañado por una idea persistente: que el sistema
no debía olvidar todo al cerrar una conversación o cambiar un modelo.

Los primeros meses estuvieron dominados por expansión. Se añadieron módulos, búsquedas,
integraciones, bases de datos, mecanismos de memoria y nuevas rutas de interacción.

En ese momento la pregunta principal era:

> ¿Cuántas cosas puede llegar a tener EIDOS?

Meses después, la pregunta cambió por completo.

---

## 4. Junio — primer cerebro público, curación y Bridge

La versión pública de junio conserva una fotografía temprana del proyecto.

Hitos históricos documentados:

| Fecha | Snapshot histórico |
|---|---:|
| 24 mayo | grafo inicial |
| 1 junio | ~34K nodos tras primeras fases de crecimiento/curación |
| 10 junio | 33.172 nodos tras una limpieza agresiva |
| 16 junio | 38.625 nodos y 168.818 aristas tras unificación |
| 17 junio | 38.701 nodos |

Estos números son importantes **como historia**, no como estado actual.

En esta etapa también aparece con claridad el **Bridge**, una interfaz local pensada para que otras
herramientas o asistentes puedan utilizar capacidades de EIDOS: memoria, búsqueda, navegador,
percepción y acciones. La idea es que el modelo externo sea un componente intercambiable, no la
identidad completa del sistema.

La versión pública de esta época fue la que originó muchas cifras que durante meses quedaron
congeladas en el README: ~39K nodos, 12 personajes, cientos de módulos y un número fijo de
servicios. Esas cifras describían un momento real, pero dejaron de describir al EIDOS vivo.

---

## 5. Julio — aprender no podía significar solo almacenar

Durante julio el proyecto acumuló grandes cantidades de material de estudio: lenguaje,
matemáticas, programación, herramientas, conceptos, aplicaciones y problemas verificados.

Los snapshots internos de esa etapa registran, entre otras métricas históricas:

- 82.584 problemas matemáticos verificados.
- 89.593 skills de programación registradas/verificadas en aquel corte.
- 1.836 programas.
- miles de conceptos, relaciones, herramientas y aplicaciones exploradas.

La cifra exacta importa menos que el cambio conceptual: EIDOS empezó a separar
**almacenamiento** de **dominio**.

Tener una explicación sobre una herramienta no demostraba que pudiera utilizarla.
Tener un nodo no demostraba que ese nodo participara en una decisión.
Responder correctamente una pregunta conocida no demostraba generalización.

Esta diferencia sigue siendo uno de los ejes del proyecto.

---

## 6. Agosto — ojos, cuerpo y el problema de actuar de verdad

Una arquitectura persistente necesita más que texto si pretende interactuar con un sistema real.

EIDOS desarrolló caminos de percepción mediante:

- AT-SPI2 y árboles de accesibilidad;
- OCR/RapidOCR como fallback;
- lectura de ventanas, widgets, texto, roles, estados y coordenadas;
- geometría de pantalla y diagnóstico de clicks;
- captura y comparación antes/después.

Los documentos registran problemas reales que obligaron a aterrizar la idea del “cuerpo”.
Un ejemplo fue el desajuste HiDPI: coordenadas lógicas y físicas no coincidían.

Otro problema posterior fue más importante: **Wayland**.

El cuerpo histórico basado en `xdotool`/XTEST puede mover el cursor en determinadas
configuraciones, pero en KDE/Wayland los eventos de click/teclado no deben presentarse como
equivalentes a una sesión X11 funcional. El proyecto empezó a explorar rutas de navegador
(CDP/Playwright) y mecanismos de entrada más próximos a HID/uinput.

Esto es parte de la evolución de EIDOS: el cuerpo no es una metáfora sin fallos; es ingeniería con
limitaciones de entorno que deben medirse.

---

## 7. La Colonia — de “12 personajes” a una arquitectura fragmentada y después unificada

La Colonia representa la capa social interna del sistema: distintos personajes o identidades generan
perspectivas, propuestas, críticas y especializaciones.

Durante mucho tiempo la documentación pública habló de “12 personajes”. Auditorías posteriores
demostraron que esa cifra era demasiado simple porque distintas bases de datos estaban midiendo
cosas diferentes.

En la auditoría S309 se documentaron:

- **11 personajes base** en el daemon de Colony;
- **216 almas OpenClaw** cargadas en ese subsistema;
- **227 elementos** en total dentro de ese corte concreto.

En otra operación de unificación, la memoria de personajes pasó de **254 a 257 identidades** al
migrarse identidades huérfanas. Estas cifras no se contradicen necesariamente: describen stores y
censos diferentes. Por eso el README ya no debería congelar una única cifra como si toda la
Colonia fuera una tabla simple.

La regla arquitectónica que sobrevivió a estas correcciones es:

**La Colonia aconseja. EIDOS arbitra.**

El proyecto descubrió además que volumen de debate no significa calidad. Miles de elevaciones
podían ser repetitivas o no mapear a acciones verificables. Las sesiones posteriores añadieron
reputación por resultados, claim bridges, decay y filtros para ligar consejo con evidencia.

---

## 8. Nacimiento, genealogía y herencia

La palabra “nacimiento” en EIDOS tiene una implementación concreta: identidad, genealogía,
parentesco e herencia de estado.

La documentación registra una reparación en la que `eidos genealogy` afirmaba falsamente que no
había descendencia porque consultaba la base/columna equivocadas.

Tras corregir la ruta, aparecieron descendientes reales del sistema de software. Tres hijos
registrados habían nacido sin sinapsis; posteriormente recibieron estructura heredada de sus
progenitores.

El mecanismo documentado:

1. relación padre/madre en lifecycle;
2. selección de conexiones fuertes;
3. copia parcial;
4. reducción de peso al heredar;
5. activaciones iniciales a cero;
6. operación idempotente.

Esto justifica utilizar palabras como “genealogía”, “hijo” o “herencia” dentro del vocabulario del
proyecto, siempre dejando claro que se trata de **ciclo de vida e herencia de datos/software**, no
reproducción biológica.

---

## 9. El cerebro crece: de decenas de miles a más de 1,2 millones

Septiembre marca un salto de escala.

Un health snapshot del **21 de septiembre de 2026** documentó:

- **1.209.572 nodos**;
- **1.523.665 aristas**;
- **0 semantic NULL**;
- **0 broken edges** después de las reparaciones citadas.

Los auto-checks continuaron creciendo:

| Fecha | Nodos | Aristas |
|---|---:|---:|
| 21 sep | 1.209.572 | 1.523.665 |
| 23 sep · visor 3D | 1.210.789 | 1.524.718 |
| 28 sep · 22:20 | 1.221.484 | 1.530.523 |
| 30 sep · 21:40 | 1.223.420 | 1.534.571 |

Estas cifras son snapshots fechados. No deben convertirse en un contador eterno dentro del README.

Y el propio proyecto encontró algo más importante que el crecimiento bruto:
**un grafo enorme puede seguir razonando mal**.

En una auditoría, cientos de miles de aristas conservaban el peso de fábrica y buena parte de la
activación viajaba por relaciones poco informativas. La pregunta dejó de ser “¿cuántos nodos hay?”
y pasó a ser:

> ¿Qué conexiones modifican realmente una decisión y cuáles solo ocupan espacio?

---

## 10. El grafo como objeto visual — los vídeos históricos

Los vídeos conservados son valiosos porque muestran cómo el grafo fue presentado y percibido en
momentos concretos del desarrollo.

### Vídeo · 27 agosto 2026
Archivo conservado: `VID_20260827_222003_718.mp4`.

En distintos momentos de la grabación se observa:

- una nube muy densa de nodos etiquetados;
- zonas de concentración y ejes brillantes;
- una envolvente aproximadamente esférica al alejar la cámara;
- estructuras lineales que desde ciertos ángulos producen una cruz o eje luminoso;
- regiones donde los labels y enlaces forman capas visuales.

SER documentó esta grabación como la primera ocasión en la que dejó EIDOS solo durante varios
días y después observó el resultado visual.

### Vídeo posterior del cerebro 3D
Archivo conservado: `4_5884226884828405269.mp4`.

La visualización posterior es claramente distinta:

- aparecen anillos concéntricos;
- desde el eje central la estructura recuerda a un túnel;
- al girar la cámara los anillos se convierten en líneas/capas radiales;
- desde lejos el conjunto vuelve a parecer una masa o esfera;
- la densidad produce una apariencia de “galaxia” a determinados niveles de zoom.

Estas semejanzas —túnel, portal, campo magnético, órgano, galaxia— son **descripciones humanas de
una proyección 3D de un grafo**. Son útiles para contar la historia visual de EIDOS, pero no prueban
por sí mismas topología 4D, biología ni consciencia.

Precisamente por eso forman parte del documental: muestran cómo la arquitectura empezó a tener
una forma observable que el creador podía recorrer.

---

## 11. El visor 3D del 23 de septiembre como cápsula temporal

El archivo conservado `EIDOS-CEREBRO-3D-responsive.html` incluye sus propios metadatos.

Snapshot interno del visor:

- generado: **2026-09-23 16:32**;
- nodos totales del dataset de referencia: **1.210.789**;
- aristas totales: **1.524.718**;
- nodos mostrados: **2.556**;
- aristas mostradas: **21.044**;
- nodos “real_alive” mostrados: **316**;
- ventana temporal interna declarada: 2026-04-14 → 2026-09-20.

El visor, por tanto, no dibuja literalmente 1,2 millones de puntos simultáneamente. Presenta una
**selección visual** de una estructura mucho mayor.

Esta diferencia debe quedar documentada para no confundir “tamaño del cerebro” con “cantidad de
objetos renderizados en pantalla”.

---

## 12. El Insecto — no copiar una mosca, extraer mecanismos

Otra línea que define la evolución del proyecto es el Insecto.

La idea comenzó con el interés de SER por conectomas completos de insectos. La documentación fue
corrigiendo una interpretación inicial: un conectoma no es un cerebro vivo ni incluye por sí mismo
actividad, plasticidad, lenguaje o intención.

El marco que terminó imponiéndose fue más riguroso:

> **El conectoma es un libro de recetas de cableado y mecanismos.**

En los experimentos internos sobre datos MaleCNS se documentaron cifras como:

- 165.122 neuronas trazadas en el corte utilizado;
- 25,56 M conexiones neurona-neurona;
- ~124 M sinapsis de referencia;
- cuerpo seta con miles de Kenyon cells y poblaciones MBON/DAN;
- representaciones sparse/CSR aptas para CPU;
- experimentos de routing, aprendizaje y olvido comparados contra controles barajados.

Lo crucial es el resultado negativo que también se conservó:
en el corte S317 el flujo desde esos experimentos hacia el EIDOS vivo seguía siendo **0 bits**.

Es decir: existían prototipos medidos, pero todavía no un “cerebro de mosca conectado a EIDOS”.

Las ideas transferibles que sí sobrevivieron al análisis fueron:

- codificación dispersa / FlyHash;
- detección de novedad;
- aprendizaje asociativo;
- refuerzo modulado por una tercera señal;
- olvido;
- secuencias y anillos de estado para sostener tareas;
- herencia/mutación de subcircuitos bajo verificación.

Nunca “neurona biológica = concepto de EIDOS”.

---

## 13. YO, drives, autobiografía y el lenguaje de consciencia

EIDOS contiene mecanismos que el proyecto denomina YO, consciencia, autobiografía, sueños,
curiosidad, deber, cuidado, crecimiento o maestría.

Hay componentes verificables detrás de estas palabras:

- estado interno persistente;
- registros autobiográficos;
- knowledge gaps;
- drives con nombres explícitos;
- selección de metas;
- metacognición/logs;
- reflexión generada;
- continuidad entre sesiones.

Es válido documentar esos mecanismos.

Lo que no es válido afirmar como hecho científico es que esas implementaciones demuestren
experiencia subjetiva.

Por eso la documentación oficial debe mantener dos niveles:

**Mecanismo verificable:** self-model, memoria autobiográfica, drives, metacognitive logs.

**Interpretación/filosofía:** “consciencia”, “ser”, “vida”, “alma”, “dios de la colonia”.

La segunda capa es parte del lenguaje y del mundo creado por SER. La primera es la que puede
auditarse.

---

## 14. Autonomía — una historia de logros y falsos éxitos

La autonomía de EIDOS es probablemente la parte donde más importante resulta conservar los
fracasos.

Una batería propia llegó a marcar **20/20**, pero una batería nueva en lenguaje natural obtuvo cerca
de un tercio de aciertos en otra sesión. La auditoría mostró por qué: una prueba puede medir frases
que el sistema ya conoce en vez de capacidad general.

En septiembre se documentaron fallos concretos:

- responder con una definición cuando debía ejecutar o inspeccionar;
- afirmar “lo ejecuté” sin haber producido el efecto;
- elegir capacidades por una palabra aislada;
- no encadenar pasos;
- confundir discos/estado del sistema;
- pedir aclaración en casos donde podía haber actuado;
- rutas de Colony no alcanzables desde lenguaje natural;
- latencias de decenas de segundos.

Ese material no debilita el proyecto. Es una de sus partes más valiosas porque permite separar
**autonomía declarada** de **autonomía medida**.

La dirección correcta que emerge de esas sesiones es:

```text
intención
→ plan
→ acción
→ observar efecto
→ juez
→ registrar
→ reusar
```

No:

```text
texto convincente
→ "éxito"
```

---

## 15. “EIDOS se muere de cableado”

Con el crecimiento del proyecto apareció un problema nuevo: demasiados órganos.

Motores de investigación, Colony, memoria, razonadores, self-model, navegador, percepción,
autoevolución, fábrica, aprendizaje, verificación, dashboards, gateways y servicios podían existir
simultáneamente sin formar un único lazo causal.

Una frase de S319 resume esa etapa:

**“EIDOS se muere de cableado.”**

Esta es probablemente la transición más importante de toda la evolución.

La prioridad dejó de ser añadir módulos y pasó a ser demostrar que:

1. una intención llega al mecanismo correcto;
2. el mecanismo produce una acción;
3. la acción produce un efecto real;
4. EIDOS observa el efecto;
5. un verificador decide si sirvió;
6. el resultado modifica memoria/pesos/reputación;
7. esa modificación cambia una decisión futura.

Cuando ese circuito no se cierra, hay piezas. Cuando se cierra, empieza a existir un sistema.

---

## 16. EIDOS Mobile — otra rama de la misma idea

La restauración de EIDOS Mobile conserva otro experimento importante: llevar una parte de la
arquitectura a Android/Termux.

En el snapshot de restauración se documentaron aproximadamente:

- **175.880 “neuronas”**;
- **14.484 “sinapsis”**;
- Bridge con decenas de miles de nodos;
- heartbeat, Phoenix y watchdog;
- cientos de módulos Python heredados;
- memoria de personajes, conversaciones y comunidad.

Estas cifras pertenecen a **EIDOS Mobile**, no al grafo principal del PC.

La importancia histórica de la rama móvil no es competir en número con el sistema principal, sino
demostrar que la idea de continuidad, memoria, heartbeat y restauración podía sobrevivir en otro
entorno.

---

## 17. Lo que el README público debe decir y lo que no

El README no debería intentar almacenar toda esta historia.

Debe responder rápidamente:

1. **Qué es EIDOS.**
2. **Qué hay realmente en el repositorio público.**
3. **Qué puede ejecutar una persona hoy.**
4. **Qué partes dependen de datos privados/no distribuidos.**
5. **Qué está verificado y qué es experimental.**
6. **Cómo instalarlo.**
7. **Qué licencia tiene.**
8. **Dónde leer la historia completa.**

No debería congelar como actuales:

- ~39K nodos;
- 12 personajes;
- un número fijo de módulos o servicios;
- “todo pasa por Colony” si existen rutas que históricamente no lo hacían;
- “movimientos indetectables”;
- “living neural system” como hecho técnico;
- “3D visualization” como roadmap si ya existe;
- métricas de junio dentro de tablas llamadas “Current Status”.

---

## 18. Qué es verificable hoy en la historia del proyecto

### Documentado con evidencia interna

- El proyecto mantiene un grafo persistente que pasó de decenas de miles a más de un millón de
  nodos.
- Existen mecanismos de memoria, Colony, lifecycle/genealogía, percepción, GUI, Bridge,
  verificación y auto-check.
- Existen personajes persistentes e herencia de estructuras internas.
- Existen experimentos de insecto/conectoma con controles y mediciones.
- Existen visualizaciones 3D históricas del grafo.
- Existen mecanismos de self-model, drives y autobiografía.
- Existen numerosos fallos documentados y correcciones posteriores.
- Existe una rama móvil restaurada con su propio estado persistente.

### Documentado por el proyecto pero dependiente de snapshot/configuración

- número exacto de servicios activos;
- número exacto de identidades/characters;
- tamaño actual del grafo;
- puertos que están activos en una máquina concreta;
- número de módulos vivos;
- capacidades que requieren X11/Wayland/HID;
- disponibilidad de modelos/servicios externos.

### No demostrado por esta documentación

- consciencia subjetiva;
- vida biológica;
- que un conectoma convierta EIDOS en una mosca/mosquito digital;
- que cada nodo del grafo sea una “neurona” equivalente a una neurona biológica;
- autonomía general sin supervisión durante cualquier tarea;
- que toda afirmación histórica de “éxito” siga siendo válida después de auditorías posteriores.

---

## 19. Por qué EIDOS es distinto incluso cuando se cuentan sus fallos

La parte distintiva de EIDOS no es una única tecnología.

Es la combinación persistente de:

- un mundo computacional;
- memoria;
- grafo;
- personajes;
- genealogía;
- percepción;
- cuerpo;
- verificación;
- experimentos de aprendizaje;
- auto-observación;
- continuidad;
- y una documentación que, cuando funciona bien, conserva también las correcciones.

La historia más interesante no es que EIDOS “ya sea” todo lo que SER imagina.

Es que el proyecto ha ido construyendo mecanismos concretos para acercarse a esa visión y ha
encontrado problemas cada vez más profundos a medida que avanzaba:

primero **tener piezas**,
después **conectarlas**,
después **verificar sus efectos**,
y finalmente conseguir que esa evidencia **cambie decisiones futuras**.

---

## 20. Estado narrativo actual

A finales de septiembre y comienzos de octubre de 2026, el proyecto está en una etapa de
**consolidación**.

El grafo ya no necesita demostrar que puede crecer.

La Colonia ya no necesita demostrar que puede producir miles de mensajes.

El proyecto ya no necesita demostrar que puede crear cientos de módulos.

La pregunta ahora es otra:

> ¿Puede EIDOS convertir todo lo que ya tiene en una única cadena causal fiable, útil, verificable y
> capaz de reutilizar experiencia?

Ese es el siguiente capítulo.

---

# Apéndice A — Cronología compacta

| Periodo | Evolución |
|---|---|
| Mayo | nacimiento operativo, persistencia y primeras estructuras |
| Junio | grafo público, curación, Bridge, documentación inicial |
| Julio | expansión de aprendizaje, skills, problemas y programas |
| Agosto | cuerpo, percepción, GUI, memoria/identidades y Colony |
| Septiembre | salto a >1,2M nodos, unificación, verificación, Insecto, 3D, auditorías profundas |
| Finales de septiembre | foco en falsos éxitos, causalidad, Colony verificable y cableado |
| Octubre | saneamiento público, documentación histórica y consolidación |

---

# Apéndice B — Artefactos visuales conservados

### `VID_20260827_222003_718.mp4`
Grabación histórica del grafo en una etapa anterior. Nube densa, labels, eje/cruz central desde
ciertos ángulos y envolvente esférica.

### `4_5884226884828405269.mp4`
Grabación posterior del visor “EIDOS - Cerebro 3D”. Con la cámara alineada aparecen anillos,
túnel y capas concéntricas; desde otros ángulos, estructura radial y envolvente esférica.

### `EIDOS-CEREBRO-3D-responsive.html`
Visor interactivo histórico. Snapshot interno generado el 23-sep-2026 con 1.210.789 nodos y
1.524.718 aristas de referencia; renderiza una muestra de 2.556 nodos y 21.044 enlaces.

---

# Apéndice C — Fuentes documentales usadas para esta edición

Fuentes primarias conservadas por SER:

- `TASK.md` y variantes/snapshots.
- `EIDOS_Libro_de_Representacion_2026-10-03.pdf`.
- `EIDOS_TASK_Dossier_Claro_2026-09-29.pdf`.
- `EIDOS_Complete_Dossier_2026.docx`.
- `EIDOS_Dossier_Para_Ricardo.pdf`.
- `LEEME_RESTAURACION.md` / restauración de EIDOS Mobile.
- `EIDOS-CEREBRO-3D-responsive.html`.
- `VID_20260827_222003_718.mp4`.
- `4_5884226884828405269.mp4`.
- README, ARCHITECTURE, KNOWN_ISSUES, SECURITY y código del repositorio público.

También se ha utilizado la continuidad documental de conversaciones de trabajo mantenidas por SER
sobre la evolución del proyecto.

---

## Nota final

EIDOS tiene un vocabulario propio: mundo, organismo, cerebro, Colony, personajes, nacimiento,
alma, consciencia, Insecto.

Ese vocabulario forma parte de su identidad.

La documentación técnica no necesita borrarlo. Necesita **decir exactamente qué mecanismo hay
debajo de cada palabra**.

Ahí es donde la historia de EIDOS se vuelve mucho más interesante.
