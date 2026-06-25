---
connector: main
description: Funnel flow between lifecycle stages (a Sankey edge list)
---
SELECT stage_from, stage_to, users
FROM user_flow
