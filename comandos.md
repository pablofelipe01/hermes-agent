# Comandos de Hermes para AROCO

Este documento describe los slash commands disponibles cuando usas Hermes a través de Telegram para operar el negocio de comercialización de cacao físico y su cobertura financiera en derivados de cacao (CC ICE NY) y USD/COP.

Hermes está instalado nativamente en el servidor; los comandos se generan automáticamente a partir de los skills definidos en `~/.hermes/skills/finance/`. Todos los datos provienen de MCPs locales conectados al gateway (`inventory`, `barchart`, `stonex`, `news`, `fx`).

---

## Resumen de comandos

| Categoría | Comando | Para qué |
|---|---|---|
| Análisis de mercado | `/analisis-general` | Brief macro completo: cacao + USD/COP + síntesis cruzada |
| Análisis de mercado | `/analisis-mercado-cacao` | Análisis profundo solo de cacao |
| Análisis de mercado | `/analisis-fx-usdcop` | Análisis profundo solo del par dólar/peso |
| Cobertura financiera | `/cobertura` | Flujo guiado paso a paso (recomendado para aprender) |
| Cobertura financiera | `/cobertura-cacao` | Versión técnica directa para usuario avanzado |
| Operación | `/inventario` | Resumen del inventario físico de cacao |
| Operación | `/cuenta` | Estado de la cuenta del broker (saldo, margen, posiciones) |
| Aprendizaje | `/glosario [término]` | Diccionario de términos financieros |
| Navegación | `/menu` | Lista de comandos disponibles |

---

## `/menu`

**Qué hace:** Muestra la lista completa de comandos disponibles con una línea de descripción cada uno.

**Cuándo usarlo:** primera interacción con el agente, cuando no recuerdas un comando, o para compartir con un colega que esté aprendiendo a usar Hermes.

**Tiempo de respuesta:** instantáneo (no consulta datos externos).

**Ejemplo de uso:**
```
Tú:  /menu
Bot: [lista categorizada de comandos]
```

---

## `/analisis-general`

**Qué hace:** brief ejecutivo combinando análisis del mercado de cacao (precio CC NY, tendencias, noticias) con análisis del par USD/COP (TRM oficial Banrep, spot intradía, tendencias, noticias macro Colombia), más una **síntesis cruzada** que evalúa cómo se ve el día desde el punto de vista de un comercializador de cacao físico colombiano.

**Cuándo usarlo:** primera cosa por la mañana para tener una vista del día. Antes de tomar una decisión grande de venta o cobertura. Cuando quieres entender si el mercado se está moviendo a tu favor o en contra.

**Tiempo de respuesta:** 5-10 minutos (consulta varias fuentes en vivo).

**Qué incluye:**
- Precio cacao actual (contrato CC más cercano).
- Tendencia 30 días con magnitud.
- Top 1–3 noticias relevantes del cacao traducidas al español.
- TRM oficial Banrep + spot mercado.
- Tendencia USD/COP a 1, 5 y 30 días.
- Top 1–3 noticias macro Colombia.
- View propio del agente: bajista/neutral/alcista en cada par.
- **Impacto cruzado para tu negocio:** identifica el cuadrante (cacao↑/↓ × USD↑/↓) y explica qué significa.
- Sugerencia operativa (no es asesoría).

**Ejemplo:**
```
Tú:  /analisis-general
Bot: [tarda ~7 min, después entrega el brief completo]
```

---

## `/analisis-mercado-cacao`

**Qué hace:** análisis profundo solo del mercado de cacao (sin la pieza FX). Útil si ya conoces tu situación FX y solo quieres entender qué pasa con el cacao.

**Cuándo usarlo:** cuando hay un evento específico del cacao (caída fuerte, subida fuerte, una huelga en países productores, decisión de la ICCO) y quieres profundizar.

**Tiempo de respuesta:** 3-5 minutos.

**Qué incluye:**
- Precio CC actual y contrato activo.
- Tendencia con cifras concretas.
- 3-5 noticias relevantes filtradas y traducidas (Costa de Marfil, Ghana, ICCO, demanda chocolatera, fondos macro).
- View propio del agente con razones concretas.

---

## `/analisis-fx-usdcop`

**Qué hace:** análisis profundo solo del par USD/COP. Crítico porque tu cacao se vende en USD y tus costos están en COP — un peso débil te ayuda, un peso fuerte te perjudica.

**Cuándo usarlo:** decisiones del Banrep (cambios de tasa), eventos macro Colombia (anuncios fiscales, paros), movimientos fuertes del peso, o si tienes deudas en USD que conviene cubrir.

**Tiempo de respuesta:** 3-5 minutos.

**Qué incluye:**
- TRM oficial Banrep (referencia fiscal/contable).
- Spot mercado en tiempo real (puede diferir varios pesos de la TRM).
- Cambios 1d / 5d / 30d en COP absoluto y porcentaje.
- Min/max del rango 30 días.
- 3-5 noticias macro relevantes.
- View propio: USD alcista / USD bajista / lateral.
- **Aplicación al negocio:** una frase de impacto contextualizada para AROCO.

---

## `/cobertura`

**Qué hace:** te guía paso a paso para armar una cobertura financiera de tu inventario físico de cacao, **sin necesidad de que conozcas términos técnicos**. El agente te hace preguntas en lenguaje normal y traduce internamente a los parámetros que necesita.

**Cuándo usarlo:** cuando tienes inventario expuesto al precio del cacao y quieres protegerte de una posible caída. Especialmente si recién estás aprendiendo derivados.

**Tiempo de respuesta:** la conversación dura ~5-10 minutos (con tus respuestas). El análisis final tarda otros 5-7 minutos en ejecutarse.

**Cómo funciona el flujo:**

1. **Saludo + lectura silenciosa de tu inventario y cuenta de broker.**
2. **Te confirma cuántas toneladas tienes disponibles.**
3. **Pregunta 1 (View de mercado):** *"¿Tú crees que el precio del cacao va a subir, bajar o quedarse parecido en los próximos meses?"*
4. **Pregunta 2 (Horizonte):** *"¿En cuánto tiempo piensas vender este cacao?"*
5. **Pregunta 3 (Tolerancia a prima):** *"¿Prefieres pagar poco aunque sacrifiques algo de protección, o pagar más para protegerte al 100%?"*
6. **Pregunta 4 (Basis):** *"El precio internacional está en ~X USD por tonelada. ¿Tu cacao se vende por encima o por debajo de eso?"*
7. **Confirmación de parámetros.**
8. **Análisis técnico.** El agente compara 3–4 estructuras (long put, bear put spread, collar, short futures) con precios reales del mercado.
9. **Recomendación amigable.** Antes de cada estructura, una línea explicando qué es. Por ejemplo:
   > *"Esta opción es como pagar un seguro: te garantiza que vas a poder vender tu cacao mínimo a X USD/t, sin importar qué pase con el precio."*
10. **Ficha de ejecución manual.** Te entrega la orden lista para que tú la coloques en la plataforma del broker. **El agente nunca ejecuta órdenes.**

---

## `/cobertura-cacao`

**Qué hace:** misma capacidad que `/cobertura` pero **sin las preguntas guiadas**. Le das los parámetros directamente en el mensaje y arranca el análisis técnico.

**Cuándo usarlo:** cuando ya conoces los términos y quieres ahorrarte el flujo conversacional. Útil para revisiones rápidas o si vas a ejecutar varias variantes seguidas.

**Ejemplo:**
```
Tú:  /cobertura-cacao basis +100, horizonte 3 meses, view bajista, prima baja
Bot: [tarda 5-7 min, entrega análisis técnico directo]
```

**Parámetros requeridos:**
- **Basis** (USD/t sobre CC, ej. `+100`, `-50`): diferencia entre tu precio local y el internacional.
- **Horizonte** (meses): cuánto falta para que vendas el cacao.
- **View** (bajista / neutral / alcista): tu vista del mercado.
- **Tolerancia a prima** (cero / baja / media / alta): cuánto estás dispuesto a pagar por protección.
- **(Opcional)** **% a cubrir** (default 100%).

---

## `/inventario`

**Qué hace:** resumen rápido del inventario físico actual: toneladas disponibles, contratos CC equivalentes, alertas si la sheet tiene problemas.

**Cuándo usarlo:** cuando necesitas saber rápido cuánto cacao tienes en bodega sin abrir Google Sheets.

**Tiempo de respuesta:** ~10-15 segundos.

**Qué incluye:**
- Toneladas totales disponibles (reconstrucción manual si la fórmula de la sheet está rota).
- Equivalente en contratos CC (1 contrato = 10 toneladas).
- Detalle de entradas vs salidas históricas.
- Alertas si hay entradas atípicas (registros sospechosos por tamaño inusual).
- Sugerencias de próximos pasos (`/cobertura`, `/cuenta`, `/analisis-general`).

---

## `/cuenta`

**Qué hace:** estado de la cuenta operativa en el broker: saldo, margen, posiciones abiertas, alertas.

**Cuándo usarlo:** antes de tomar una nueva posición. Para verificar margen disponible. Si te llega notificación del broker.

**Tiempo de respuesta:** ~10-20 segundos.

**Qué incluye:**
- **Estado general:** saludable / atención / margin call (indicador visual).
- **NLV** en tiempo real vs cierre del día anterior.
- **Margen inicial requerido** vs disponible.
- **Excedente / déficit** de margen.
- **Posiciones abiertas** (si están disponibles en la API): producto, cantidad, dirección, vencimiento, precio promedio, P&L latente.
- **Alertas operativas** si hay margin call activo o NLV bajo.

**Importante:** este comando es solo lectura. Nunca ejecuta órdenes ni modifica nada en la cuenta del broker.

---

## `/glosario [término]`

**Qué hace:** diccionario interactivo de términos financieros relevantes para cobertura de cacao. Sin argumento, lista todos los términos disponibles agrupados por categoría. Con argumento, devuelve definición + ejemplo numérico.

**Cuándo usarlo:** cualquier momento que no entiendas un término técnico que aparece en otro comando.

**Tiempo de respuesta:** instantáneo.

**Ejemplos:**
```
/glosario              → lista todos los términos
/glosario put          → definición + ejemplo de un put
/glosario basis        → definición + ejemplo de basis
/glosario collar       → cómo funciona un collar y para qué sirve
```

**Términos disponibles:**

- **Conceptos base:** basis, prima, strike, view, horizonte.
- **Instrumentos:** futuro, put, call, collar, bear put spread, three-way fence.
- **Métricas y griegas:** delta, gamma, theta, vega, IV (volatilidad implícita), ITM, ATM, OTM.
- **Operativa y riesgo:** margen, margin call, roll, breakeven, upside, downside.
- **Mercado:** TRM, spot, contrato CC, ICE NY, tick, expiration.

Cada definición es breve y va acompañada de un ejemplo numérico simple — el objetivo es que un comercializador físico que está aprendiendo derivados pueda visualizar el concepto, no aprender la fórmula matemática.

---

## Convenciones del agente

**Tono educativo siempre.** Cada vez que aparece un término técnico nuevo en una conversación, el agente lo introduce con una micro-definición la primera vez. Después puede usarlo libremente.

**Solo análisis, nunca ejecución.** Ningún comando coloca órdenes en el broker. Cuando una estructura está lista para ejecutar, el agente entrega una "ficha de orden" en texto que tú copias y pegas en la plataforma del broker manualmente.

**Datos en vivo.** Todos los análisis usan datos actuales de fuentes públicas (cadena de opciones de la bolsa de futuros, TRM oficial, noticias en tiempo real). El agente nunca inventa precios o cifras; si una fuente falla, lo reporta honestamente.

**Sin asesoría regulada.** Los comandos entregan análisis cuantitativo. La decisión y ejecución son responsabilidad del usuario.

**Formato Telegram.** Las respuestas usan markdown ligero (negritas con `*texto*`, bullets con `•`, emojis solo en encabezados de sección para escaneo rápido). Sin tablas pipe porque Telegram las reformatea mal.

**Límite de mensaje Telegram (4,096 caracteres).** Si una respuesta excede, Telegram la parte en varios mensajes consecutivos.

---

## Si algo no funciona

1. **El comando no responde:** abrir y cerrar el chat de Telegram. Si persiste, escribir el comando a mano (sin usar el botón del menú).
2. **Una fuente externa falla** (la bolsa, las noticias, el broker): el agente lo reporta y completa lo que sí pudo obtener. No se cae el comando entero.
3. **Tienes una duda no cubierta por un comando:** escribe al agente en lenguaje normal. Hermes entiende lenguaje natural y va a tratar de ayudarte, llamando los skills relevantes según haga falta.

---

## Cómo extender

Cada comando corresponde a un archivo `SKILL.md` en `~/.hermes/skills/finance/<nombre>/`. Para crear un comando nuevo:

1. Crear `~/.hermes/skills/<categoría>/<nombre>/SKILL.md` con frontmatter `name: <nombre>` y `description: …`.
2. Documentar el workflow, las tools que invoca, las restricciones y el formato de salida.
3. Hermes detecta el skill al vuelo. El botón del menú de Telegram puede aparecer al siguiente restart del gateway.

Para más detalle sobre el patrón de extensión de Hermes vía MCP servers, ver `ejemplos.md`.
