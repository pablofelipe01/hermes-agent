# hermes-agent

Playbook de despliegue del agente Hermes en producción AROCO.

- [`instrucciones.md`](./instrucciones.md) — guía paso a paso completa
  (Telegram + OpenRouter + systemd, instalación nativa en `~/.hermes/`).
- [`ejemplos.md`](./ejemplos.md) — patrón para extender Hermes con
  capacidades nuevas vía MCP (plantillas de server, Docker, deploy y
  registro).
- [`replicar-agente-cliente.md`](./replicar-agente-cliente.md) — levantar un
  segundo/tercer agente Hermes aislado en el mismo servidor (patrón Jerry /
  Renata vía `HERMES_HOME`), sin tocar los demás.
