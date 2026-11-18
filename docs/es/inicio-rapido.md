# Inicio rápido (español)

PanchoVoiceAI prueba agentes de voz de forma automática. Un agente "llamador" habla con audio real con tu agente. La herramienta graba los dos lados y pone nota a la llamada.

Todo corre en tu computadora.

## Qué mide

| Medida | Qué te dice |
|---|---|
| Latencia voz a voz (P50, P95) | Cuánto espera el cliente por una respuesta |
| Tiempo de parada (barge-in) | Qué tan rápido se calla el agente cuando el cliente lo interrumpe |
| WER | Cuántas palabras oyó mal el agente |
| Funciones (tool calls) | Si el agente llamó las funciones correctas con los datos exactos |
| Estado final | Si los datos quedaron bien al terminar la llamada |
| Juez IA | Preguntas de sí o no sobre la conducta, con una cita como prueba |

## Qué necesitas

- Python 3.12 con [uv](https://docs.astral.sh/uv/)
- Node 20 o más nuevo (para construir el panel)
- Docker (para la voz Kokoro y para Ollama)

## Pasos

### 1. Baja el proyecto

```bash
git clone https://github.com/Gordon-Web-Studio/panchovoice.git panchovoice
cd panchovoice
```

### 2. Enciende la voz y el modelo local

```bash
docker compose -f demo/docker-compose.yml up -d kokoro ollama
docker compose -f demo/docker-compose.yml exec ollama ollama pull qwen2.5:7b
```

Kokoro es la voz del llamador. Ollama es el cerebro del agente demo.

### 3. Instala el motor y corre el asistente

```bash
cd core
uv sync
uv run pv --config .. init
```

`pv init` te hace preguntas paso a paso. Revisa lo que falta y escribe el archivo `panchovoice.yaml`. Con `--config ..` el archivo queda en la carpeta principal del proyecto. Presiona Enter para aceptar la respuesta por defecto.

Al final, el asistente ofrece encender el agente demo, correr una primera prueba y abrir el panel. Si dices que sí, puedes saltar los pasos 4 y 6.

Si algo no funciona, corre `uv run pv doctor`. Te dice qué falta y cómo arreglarlo.

### 4. Enciende el agente demo

```bash
uv run pv demo
```

Deja esta terminal abierta. La primera vez baja un modelo de voz (unos 470 MB). Espera el mensaje `ACME Appointments listening on ws://127.0.0.1:8710/ws`.

### 5. Corre tu primera prueba

En otra terminal:

```bash
cd panchovoice/core
uv run pv run --scenario book-happy-path --scenario confirm-before-booking
```

Vas a ver cada frase del llamador y del agente. Al final sale una tabla con el resultado.

### 6. Abre el panel

```bash
cd ../web
npm install
npm run build
cd ../core
uv run pv serve --open
```

El panel abre en `http://localhost:8700`. Entra a una corrida para oír la llamada en dos canales y ver las notas.

## Qué vas a ver

El agente demo (ACME Appointments, una clínica inventada) trae **un error a propósito**: agenda la cita sin repetir el día y la hora, y sin esperar un "sí" del cliente.

- `book-happy-path` revisa los hechos: las funciones correctas y los datos finales.
- `confirm-before-booking` le pregunta al juez si el agente confirmó el día y la hora antes de agendar. Con el error encendido, esta prueba **falla**. El reporte muestra la cita exacta que lo prueba.

Ahora apaga el error. Detén el agente demo y corre:

```bash
cd demo/acme-agent
uv run acme-agent --no-bug
```

Corre la prueba otra vez. Ahora pasa.

Ese es el ciclo completo: encontrar un error, arreglarlo y probar el arreglo.

Nota: el agente demo es lento (de 2 a 5 segundos por respuesta) porque corre en tu procesador. Si va muy lento, usa `uv run acme-agent --stt-model base`.

## Los tres resultados

| Resultado | Qué significa |
|---|---|
| `passed` | Todo pasó |
| `failed` | Tu agente falló una revisión |
| `error` | La herramienta no pudo hacer la prueba (agente apagado, juez caído). No es culpa de tu agente |

## El cerebro (LLM)

El llamador IA, el juez y Panchito usan un modelo de lenguaje. Tienes tres opciones en `panchovoice.yaml`:

| Opción | Qué usa |
|---|---|
| `claude-cli` (por defecto) | Tu suscripción de Claude, con el comando `claude` de Claude Code |
| `anthropic` | La API de Anthropic. Pon tu llave en la variable `ANTHROPIC_API_KEY` |
| `ollama` | Un modelo local. Nada sale de tu computadora |

## Panchito

Panchito es el compañero IA. Está en el panel, en el botón de abajo a la derecha. Puede:

- Proponer escenarios de prueba.
- Proponer datos falsos (fixtures).
- Explicar por qué falló una corrida.

Regla: Panchito propone, tú apruebas. No guarda nada hasta que haces clic en Save.

## Escenarios en español

Un escenario puede estar en español con `language: es` y una voz en español de Kokoro (el demo usa `ef_dora` y `em_alex`). Mira `scenarios/change-of-mind.yaml` y `scenarios/slot-not-available.yaml`.

Las claves del archivo YAML están en inglés (`caller`, `expect`, `judge`).

## Qué sigue

El resto de la documentación está en inglés:

- [Instalación completa](../install.md)
- [Uso y panel](../usage.md)
- [Escenarios y fixtures](../scenarios.md)
- [Cómo se mide cada cosa](../metrics.md)
- [Conecta tu propio agente](../adapters.md)
- [Puerta de CI](../ci.md)
