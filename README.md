# hermes-agent

Playbook de despliegue del agente Hermes en producción AROCO.

- [`instrucciones.md`](./instrucciones.md) — guía paso a paso completa
  (Telegram + OpenRouter + systemd, instalación nativa en `~/.hermes/`).
- [`ejemplos.md`](./ejemplos.md) — patrón para extender Hermes con
  capacidades nuevas vía MCP (plantillas de server, Docker, deploy y
  registro).
- [`patrones-operacionales.md`](./patrones-operacionales.md) — patrones probados
  en producción (aislamiento entre agentes, MCPs con sesión, zombies de
  navegador, sandbox de Chromium…). Empezar por acá cuando algo falla raro.
- [`replicar-agente-cliente.md`](./replicar-agente-cliente.md) — levantar un
  segundo/tercer agente Hermes aislado en el mismo servidor (patrón Renata
  vía `HERMES_HOME`), sin tocar los demás.
- [`renata-notetaker-reuniones.md`](./renata-notetaker-reuniones.md) — flujo para
  que Renata asista a los Google Meet de AROCO, transcriba y resuma (gcalendar +
  meet bot + cron/skill). Fase 1 desplegada.
