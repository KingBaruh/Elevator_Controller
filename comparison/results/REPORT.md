# Policy comparison results

Generated with `py -m comparison`. See [methodology](../README.md).

Mixed-traffic cells are equal-weight means of run-level metrics across seeds; scripted scenarios have one run.

| Scenario | Policy | Hall completed | Hall pending | Hall mean wait, served (s) | Oldest pending (s) | Cabin mean, served (s) | Distance (floors) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direction change | Strict internal priority | 1.00 | 0.00 | 15.60 | 0.00 | 5.53 | 13.00 |
| Direction change | Service sweep + aging | 1.00 | 0.00 | 8.60 | 0.00 | 9.53 | 14.00 |
| Repeated cabin requests | Strict internal priority | 0.00 | 1.00 | N/A | 119.00 | 4.90 | 48.00 |
| Repeated cabin requests | Service sweep + aging | 1.00 | 0.00 | 13.50 | 0.00 | 5.57 | 52.00 |
| Mixed traffic | Strict internal priority | 20.80 | 0.00 | 27.18 | 0.00 | 12.11 | 49.80 |
| Mixed traffic | Service sweep + aging | 21.40 | 0.00 | 25.91 | 0.00 | 11.32 | 54.60 |

N/A means no completed requests, not zero waiting. Pending ages are censored at the run horizon.

Calls are deduplicated by floor/direction; cabin requests by destination. Equal passenger demand can produce different request counts.

The comparison changes both direction persistence and hall-call aging. It does not isolate their individual effects.

![Summary](figures/summary.png)

![Direction change](figures/direction_change.png)

![Repeated cabin requests](figures/repeated_cabin.png)

![Mixed traffic](figures/mixed_traffic.png)
