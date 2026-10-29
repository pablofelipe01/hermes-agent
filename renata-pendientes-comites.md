# Renata — pendientes y agenda de los comités semanales

MCP + dos skills para que **Renata** (segundo agente AROCO, ver
[replicar-agente-cliente.md](./replicar-agente-cliente.md)) lleve la agenda de los
comités semanales. Alguien le deja un tema por Signal, ella lo propone antes de la
reunión y, al hacer el acta, marca qué se trató.

> Los números de teléfono, correos de asistentes e IDs de cron/evento van como
> placeholders `<ASÍ>`. Los valores reales viven solo en el servidor.

## Qué resuelve

Pedido de Pablo (2026-10-29): *"que Álvaro le pueda decir a Renata 'para la
siguiente reunión comercial debemos discutir las necesidades de inventario de
reserva', y que cuando llegue la reunión Renata proponga los pendientes"*.

```
Álvaro (Signal) ──► registrar_pendiente ──► "Anotado para el Comité Comercial del martes 3-nov 11:00"
                                             │
cron 17:00, día hábil anterior ──► agendas_por_enviar ──► correo a asistentes + aviso Signal
                                             │
notetaker resumen (después del Meet) ──► pendientes_de_reunion / cerrar_reunion
                                             ├─ tratados → se cierran (con nota de qué se decidió)
                                             └─ no tratados → siguen abiertos → próxima agenda
```

## Piezas

| Pieza | Qué hace | Dónde |
|-------|----------|-------|
| `renata-pendientes-mcp` | SQLite de pendientes + lectura del calendario de Renata | `~/projects/agents/renata-pendientes-mcp/`, `127.0.0.1:8786` |
| skill `agenda-comites` | Cron: arma y envía la agenda | `~/.hermes-renata/skills/note-taking/` |
| skill `reunion-resumen` (paso c2) | Cierra la agenda al hacer el acta | ídem |
| cron "Agenda comités" | `0 17 * * 1-5`, deliver `signal:<pablo>,signal:<alvaro>` | `~/.hermes-renata/cron/jobs.json` |

Tools: `registrar_pendiente`, `listar_pendientes`, `editar_pendiente`,
`agendas_por_enviar`, `marcar_agenda_enviada`, `pendientes_de_reunion`,
`cerrar_reunion`. Datos en `~/projects/data/renata-pendientes/pendientes.db`;
calendario con el token de `renata-gcalendar-mcp` montado `:ro`.

## Decisiones de diseño

**El camino correcto es una tool, no prosa.** Anotar, listar y cerrar son tools
deterministas con descripciones que dicen cuándo usarlas ("para la próxima
reunión X hay que…"). Ver *la tool le gana a la instrucción* en
[patrones-operacionales.md](./patrones-operacionales.md): si el registro fuera
"escribe en un archivo" dentro de un skill, el agente terminaría usando su memoria
interna, que se compacta y pierde cosas.

**Pendientes por comité, no por fecha.** Un pendiente abierto pertenece siempre a
la próxima ocurrencia de su comité. "Pasar a la siguiente reunión" no mueve nada:
basta con no cerrarlo (se suma `veces_aplazado`, que sale en la agenda).

**Qué es un comité.** Un dict `COMITES` en `server.py`: nombre exacto del
calendario → alias con que la gente lo nombra. Todo lo de ventas va al Comité
Comercial.

| Comité | Alias |
|---|---|
| Comité Comercial | comercial, ventas, comité de ventas |
| Comité Operativo | operativo, operaciones |
| Comité Financiero | financiero, finanzas |
| Comité Contable | contable, contabilidad |
| Revisión MEGA y Palancas | mega, palancas |
| Revisión CRM y Plataformas | crm, plataformas |

Un evento del calendario cuenta como comité solo si su título **normalizado
(sin tildes ni mayúsculas) es exactamente** el nombre y es parte de una **serie
recurrente**. Así una reunión suelta como "Reunión comercial AROCO y <cliente>" no
se confunde con el Comité Comercial. Si lo que dice la persona es ambiguo o no
coincide con ningún comité, la tool devuelve `ok=false` y le pide a Renata que
pregunte en vez de adivinar.

**Agenda: día hábil anterior, 17:00.** El viernes cubre hasta el lunes. Sin
pendientes no se manda nada. `marcar_agenda_enviada` evita reenvíos y solo se
llama si el correo salió.

**Cierre conservador.** Renata decide, leyendo la transcripción, qué pendientes
se trataron; ante la duda, el tema **no** está tratado y pasa a la próxima.
`cerrar_reunion` es idempotente: si el cron del resumen corre dos veces, no aplaza
dos veces. Solo cuenta los pendientes anotados **antes** del inicio de la reunión.

## Instrucciones para el usuario

> Escríbele a Renata por Signal: *"Para el Comité Comercial: hay que discutir …"*.
> Te confirma comité y fecha. Un tema por frase. *"¿Qué tenemos para el
> comercial?"* muestra la lista; también puedes pedirle que corrija, quite o mueva
> un tema. El día hábil anterior a las 5 pm llega la agenda por correo; después
> del comité el acta dice qué se trató, y lo que no se vio pasa al siguiente.

## Despliegue (2026-10-29)

1. `docker compose up -d --build` en el repo del MCP.
2. `mcp_servers.renata-pendientes: { url: http://localhost:8786/mcp }` en
   `~/.hermes-renata/config.yaml`.
3. **Con el gateway detenido**: `hermes cron create "0 17 * * 1-5" … --skill
   agenda-comites --deliver …`, y luego editar `jobs.json` a mano para fijar
   `enabled_toolsets` (el CLI no tiene flag para eso):
   - "Agenda comités" → `["mcp-renata-pendientes", "mcp-renata-gmail"]`
   - "Notetaker resumen" → agregar `mcp-renata-pendientes`. **Sin esto, el paso
     c2 del skill nombra tools que el cron no tiene y el modelo las alucina** (ver
     [cronjobs.md](./cronjobs.md)). El skill y el toolset se cambian juntos.
4. `systemctl start hermes-renata-gateway` → en el log: `MCP server
   'renata-pendientes' (HTTP): registered … tool(s)`.
5. Prueba: `HERMES_HOME=~/.hermes-renata … -z "Para la siguiente reunión
   comercial debemos discutir X (PRUEBA)" -t mcp-renata-pendientes` → respondió
   "Anotado para el Comité Comercial del martes 3-nov a las 11:00". Borrar el
   registro de prueba de la base después.

## Hueco conocido

Un tema que se deja sábado o domingo para un comité del lunes no sale en la
agenda (el cron del viernes ya corrió). Sí aparece en el cierre del acta y pasa
al siguiente comité. Si molesta, agregar una corrida extra el domingo.

## Agregar un comité nuevo

Agregar la entrada a `COMITES` (título **exacto** del evento recurrente + alias),
`docker compose up -d --build`. No hace falta reiniciar Renata: la lista de tools
no cambia.
