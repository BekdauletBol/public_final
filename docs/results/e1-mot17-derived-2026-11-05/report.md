# Evaluation report e1-mot17-derived-2026-11-05

> **Sanity check only.** At least one zone uses `source=derived` ground truth
> (MOT17 GT box bottom-centre inside the polygon). That definition is the
> `bottom_center` rule applied to the GT box, so it favours the foot-point
> rules and must not be used for the headline comparison (spec I.3).

| Item | Value |
|---|---|
| Started (UTC) | 2026-11-05T13:12:50.594935+00:00 |
| Host / platform | MacBook-Air-Ten.local / macOS-26.2-arm64-arm-64bit |
| Device | mps |
| Model SHA-256 | f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 |
| Git commit | ba6ec5018b17a8297ea3ade147371e83ffcfcfa1 |
| Config SHA-256 | 85a4fb0d5ff846881bcaa5c72cd613edd9194df9d833d4229df4f79cadcc991d |
| Sequences | MOT17-02, MOT17-04, MOT17-09 |
| Detector | conf 0.3, imgsz 640 |
| Matching | IoU 0.5, classes [1], min visibility 0.25 |
| State machine / tolerance | 3 in / 5 out, ±10 frames |
| Bootstrap | 5000 resamples, seed 1234 |
| Invariant violations | 0 |
| GT persons without a label | 0 |

## Per-person membership, pooled over all sequences and zones

| Rule | Precision [95 % CI] | Recall [95 % CI] | F1 [95 % CI] | FPR | Recall (matched) | TP | FP | FN | FN missed det. | Event P | Event R | Event F1 | Dup. rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bbox_overlap | 0.454 [0.446, 0.462] | 0.635 [0.622, 0.649] | 0.530 [0.522, 0.537] | 0.204 | 0.983 | 12280 | 14755 | 7054 | 6837 | 0.500 | 0.304 | 0.378 | 0.000 |
| bottom_center | 0.920 [0.915, 0.925] | 0.616 [0.604, 0.630] | 0.738 [0.728, 0.748] | 0.014 | 0.953 | 11913 | 1034 | 7421 | 6837 | 0.630 | 0.739 | 0.680 | 0.000 |
| three_point_any | 0.804 [0.797, 0.810] | 0.620 [0.607, 0.633] | 0.700 [0.691, 0.709] | 0.040 | 0.959 | 11981 | 2929 | 7353 | 6837 | 0.538 | 0.609 | 0.571 | 0.038 |
| three_point_majority | 0.921 [0.916, 0.926] | 0.615 [0.602, 0.628] | 0.737 [0.728, 0.747] | 0.014 | 0.951 | 11888 | 1020 | 7446 | 6837 | 0.583 | 0.609 | 0.596 | 0.000 |
| three_point_all | 0.934 [0.929, 0.938] | 0.563 [0.552, 0.576] | 0.703 [0.693, 0.712] | 0.011 | 0.872 | 10893 | 775 | 8441 | 6837 | 0.533 | 0.696 | 0.604 | 0.000 |
| bottom_strip | 0.879 [0.874, 0.885] | 0.613 [0.601, 0.627] | 0.723 [0.714, 0.732] | 0.022 | 0.949 | 11860 | 1630 | 7474 | 6837 | 0.538 | 0.609 | 0.571 | 0.000 |

Recall counts persons the detector missed as FN (`FN missed det.`); *Recall (matched)*
leaves them out, so it isolates the rule from the detector.

### E1 check on this data source

| Rule | Precision gain vs bbox_overlap | CIs separated | Recall loss | Meets E1 |
|---|---|---|---|---|
| bottom_center | 0.466 | True | 0.019 | True |
| three_point_any | 0.349 | True | 0.015 | True |
| three_point_majority | 0.467 | True | 0.020 | True |
| three_point_all | 0.479 | True | 0.072 | False |
| bottom_strip | 0.425 | True | 0.022 | True |

E1 needs the criterion on at least 2 of 3 data sources (MOT17, WILDTRACK, own
recordings); this table covers one.

## Per sequence and zone (precision / recall / F1)

| Sequence | Zone | bbox_overlap | bottom_center | three_point_any | three_point_majority | three_point_all | bottom_strip |
|---|---|---|---|---|---|---|---|
| MOT17-02 | corner | 0.000 / - / 0.000 | - / - / - | 0.000 / - / 0.000 | - / - / - | - / - / - | - / - / - |
| MOT17-02 | irregular | 0.657 / 0.292 / 0.404 | 0.944 / 0.291 / 0.444 | 0.902 / 0.292 / 0.441 | 0.944 / 0.291 / 0.444 | 0.948 / 0.277 / 0.429 | 0.923 / 0.292 / 0.443 |
| MOT17-02 | strip | 0.563 / 0.818 / 0.667 | 0.880 / 0.814 / 0.846 | 0.861 / 0.815 / 0.837 | 0.880 / 0.814 / 0.846 | 0.921 / 0.799 / 0.855 | 0.850 / 0.808 / 0.828 |
| MOT17-04 | corner | 0.367 / 0.518 / 0.429 | 0.943 / 0.515 / 0.666 | 0.846 / 0.517 / 0.641 | 0.943 / 0.515 / 0.666 | 0.955 / 0.471 / 0.631 | 0.954 / 0.441 / 0.603 |
| MOT17-04 | irregular | 0.669 / 0.896 / 0.766 | 0.962 / 0.885 / 0.922 | 0.912 / 0.895 / 0.904 | 0.962 / 0.885 / 0.922 | 0.981 / 0.733 / 0.839 | 0.932 / 0.853 / 0.890 |
| MOT17-04 | strip | 0.345 / 0.937 / 0.504 | 0.901 / 0.805 / 0.851 | 0.581 / 0.805 / 0.675 | 0.901 / 0.805 / 0.851 | 0.903 / 0.805 / 0.851 | 0.842 / 0.890 / 0.865 |
| MOT17-09 | corner | 0.007 / 0.750 / 0.015 | 0.361 / 0.650 / 0.464 | 0.125 / 0.650 / 0.210 | 0.308 / 0.200 / 0.242 | 0.000 / 0.000 / 0.000 | 0.256 / 0.550 / 0.349 |
| MOT17-09 | irregular | 0.224 / 0.917 / 0.360 | 0.853 / 0.901 / 0.876 | 0.654 / 0.915 / 0.763 | 0.848 / 0.866 / 0.856 | 0.863 / 0.653 / 0.744 | 0.696 / 0.887 / 0.780 |
| MOT17-09 | strip | 0.606 / 0.912 / 0.728 | 0.897 / 0.907 / 0.902 | 0.826 / 0.912 / 0.867 | 0.897 / 0.907 / 0.902 | 0.898 / 0.834 / 0.865 | 0.854 / 0.907 / 0.880 |

## Detector (YOLOv8n) against MOT17 boxes at IoU 0.5

| Sequence | Frames | GT persons | Matched | Recall | Precision |
|---|---|---|---|---|---|
| MOT17-02 | 600 | 9393 | 3883 | 0.413 | 0.843 |
| MOT17-04 | 1050 | 39821 | 18163 | 0.456 | 0.911 |
| MOT17-09 | 525 | 3745 | 3356 | 0.896 | 0.888 |

## Frame-level occupancy, pooled

| Rule | TP | FP | FN | TN | Precision | Recall |
|---|---|---|---|---|---|---|
| bbox_overlap | 4541 | 1810 | 0 | 174 | 0.715 | 1.000 |
| bottom_center | 4232 | 71 | 309 | 1913 | 0.983 | 0.932 |
| three_point_any | 4382 | 285 | 159 | 1699 | 0.939 | 0.965 |
| three_point_majority | 4220 | 58 | 321 | 1926 | 0.986 | 0.929 |
| three_point_all | 4015 | 27 | 526 | 1957 | 0.993 | 0.884 |
| bottom_strip | 4267 | 160 | 274 | 1824 | 0.964 | 0.940 |

## McNemar exact test, pooled (p-value; b = row right & column wrong, c = reverse)

|  | bbox_overlap | bottom_center | three_point_any | three_point_majority | three_point_all | bottom_strip |
|---|---|---|---|---|---|---|
| bbox_overlap | — | 0 (367/13721) | 0 (299/11826) | 0 (392/13735) | 0 (1387/13980) | 0 (420/13125) |
| bottom_center | 0 (13721/367) | — | 0 (1895/68) | 0.11 (25/14) | 5.3e-107 (1020/259) | 1.5e-70 (1017/368) |
| three_point_any | 0 (11826/299) | 0 (68/1895) | — | 0 (93/1909) | 1.7e-79 (1088/2154) | 5.1e-113 (811/1989) |
| three_point_majority | 0 (13735/392) | 0.11 (14/25) | 0 (1909/93) | — | 1.8e-107 (995/245) | 1.4e-66 (1027/389) |
| three_point_all | 0 (13980/1387) | 5.3e-107 (259/1020) | 1.7e-79 (2154/1088) | 1.8e-107 (245/995) | — | 0.025 (1169/1281) |
| bottom_strip | 0 (13125/420) | 1.5e-70 (368/1017) | 5.1e-113 (1989/811) | 1.4e-66 (389/1027) | 0.025 (1281/1169) | — |

## Boundary analysis, pooled (error rate by GT bottom-centre distance, px)

Matched persons only. Positive distance = inside the zone; the outermost bins
include everything beyond ±100 px.

| Bin (px) | n | bbox_overlap | bottom_center | three_point_any | three_point_majority | three_point_all | bottom_strip |
|---|---|---|---|---|---|---|---|
| -100 | 50524 | 0.116 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| -90 | 1057 | 0.293 | 0.000 | 0.003 | 0.000 | 0.000 | 0.000 |
| -80 | 1226 | 0.308 | 0.000 | 0.002 | 0.000 | 0.000 | 0.000 |
| -70 | 1281 | 0.314 | 0.000 | 0.003 | 0.000 | 0.000 | 0.000 |
| -60 | 1183 | 0.358 | 0.000 | 0.008 | 0.000 | 0.000 | 0.001 |
| -50 | 2507 | 0.609 | 0.000 | 0.011 | 0.000 | 0.000 | 0.001 |
| -40 | 1506 | 0.528 | 0.003 | 0.207 | 0.003 | 0.003 | 0.013 |
| -30 | 1827 | 0.756 | 0.002 | 0.515 | 0.002 | 0.001 | 0.030 |
| -20 | 1474 | 0.768 | 0.014 | 0.190 | 0.014 | 0.009 | 0.154 |
| -10 | 1124 | 0.733 | 0.201 | 0.461 | 0.189 | 0.087 | 0.464 |
| 0 | 1225 | 0.104 | 0.401 | 0.346 | 0.414 | 0.679 | 0.335 |
| 10 | 958 | 0.048 | 0.050 | 0.049 | 0.055 | 0.389 | 0.173 |
| 20 | 1001 | 0.031 | 0.033 | 0.033 | 0.037 | 0.295 | 0.046 |
| 30 | 752 | 0.011 | 0.011 | 0.011 | 0.011 | 0.094 | 0.012 |
| 40 | 884 | 0.002 | 0.002 | 0.002 | 0.002 | 0.017 | 0.003 |
| 50 | 978 | 0.000 | 0.000 | 0.000 | 0.000 | 0.013 | 0.001 |
| 60 | 847 | 0.000 | 0.000 | 0.000 | 0.000 | 0.004 | 0.000 |
| 70 | 865 | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 |
| 80 | 1074 | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 |
| 90 | 3913 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

## Hysteresis sweep (E3), pooled event metrics

| Rule | Enter | Exit | Pred events | Truth events | Event P | Event R | Event F1 | Dup. rate |
|---|---|---|---|---|---|---|---|---|
| bbox_overlap | 1 | 1 | 30 | 25 | 0.233 | 0.280 | 0.255 | 0.000 |
| bottom_center | 1 | 1 | 79 | 25 | 0.291 | 0.920 | 0.442 | 0.114 |
| three_point_any | 1 | 1 | 67 | 25 | 0.269 | 0.720 | 0.391 | 0.149 |
| three_point_majority | 1 | 1 | 77 | 25 | 0.273 | 0.840 | 0.412 | 0.130 |
| three_point_all | 1 | 1 | 82 | 25 | 0.220 | 0.720 | 0.336 | 0.073 |
| bottom_strip | 1 | 1 | 71 | 25 | 0.254 | 0.720 | 0.375 | 0.113 |
| bbox_overlap | 1 | 5 | 16 | 25 | 0.438 | 0.280 | 0.341 | 0.000 |
| bottom_center | 1 | 5 | 41 | 25 | 0.463 | 0.760 | 0.576 | 0.049 |
| three_point_any | 1 | 5 | 38 | 25 | 0.395 | 0.600 | 0.476 | 0.079 |
| three_point_majority | 1 | 5 | 39 | 25 | 0.436 | 0.680 | 0.531 | 0.077 |
| three_point_all | 1 | 5 | 46 | 25 | 0.391 | 0.720 | 0.507 | 0.065 |
| bottom_strip | 1 | 5 | 40 | 25 | 0.400 | 0.640 | 0.492 | 0.075 |
| bbox_overlap | 1 | 10 | 12 | 20 | 0.583 | 0.350 | 0.438 | 0.000 |
| bottom_center | 1 | 10 | 30 | 20 | 0.533 | 0.800 | 0.640 | 0.000 |
| three_point_any | 1 | 10 | 24 | 20 | 0.500 | 0.600 | 0.545 | 0.000 |
| three_point_majority | 1 | 10 | 27 | 20 | 0.519 | 0.700 | 0.596 | 0.000 |
| three_point_all | 1 | 10 | 29 | 20 | 0.448 | 0.650 | 0.531 | 0.000 |
| bottom_strip | 1 | 10 | 26 | 20 | 0.538 | 0.700 | 0.609 | 0.000 |
| bbox_overlap | 3 | 1 | 19 | 23 | 0.368 | 0.304 | 0.333 | 0.000 |
| bottom_center | 3 | 1 | 41 | 23 | 0.488 | 0.870 | 0.625 | 0.000 |
| three_point_any | 3 | 1 | 39 | 23 | 0.436 | 0.739 | 0.548 | 0.051 |
| three_point_majority | 3 | 1 | 39 | 23 | 0.436 | 0.739 | 0.548 | 0.026 |
| three_point_all | 3 | 1 | 43 | 23 | 0.372 | 0.696 | 0.485 | 0.000 |
| bottom_strip | 3 | 1 | 36 | 23 | 0.444 | 0.696 | 0.542 | 0.000 |
| bbox_overlap | 3 | 5 | 14 | 23 | 0.500 | 0.304 | 0.378 | 0.000 |
| bottom_center | 3 | 5 | 27 | 23 | 0.630 | 0.739 | 0.680 | 0.000 |
| three_point_any | 3 | 5 | 26 | 23 | 0.538 | 0.609 | 0.571 | 0.038 |
| three_point_majority | 3 | 5 | 24 | 23 | 0.583 | 0.609 | 0.596 | 0.000 |
| three_point_all | 3 | 5 | 30 | 23 | 0.533 | 0.696 | 0.604 | 0.000 |
| bottom_strip | 3 | 5 | 26 | 23 | 0.538 | 0.609 | 0.571 | 0.000 |
| bbox_overlap | 3 | 10 | 12 | 20 | 0.583 | 0.350 | 0.438 | 0.000 |
| bottom_center | 3 | 10 | 22 | 20 | 0.682 | 0.750 | 0.714 | 0.000 |
| three_point_any | 3 | 10 | 19 | 20 | 0.579 | 0.550 | 0.564 | 0.000 |
| three_point_majority | 3 | 10 | 19 | 20 | 0.632 | 0.600 | 0.615 | 0.000 |
| three_point_all | 3 | 10 | 24 | 20 | 0.542 | 0.650 | 0.591 | 0.000 |
| bottom_strip | 3 | 10 | 21 | 20 | 0.571 | 0.600 | 0.585 | 0.000 |
| bbox_overlap | 5 | 1 | 16 | 21 | 0.438 | 0.333 | 0.378 | 0.000 |
| bottom_center | 5 | 1 | 37 | 21 | 0.486 | 0.857 | 0.621 | 0.000 |
| three_point_any | 5 | 1 | 33 | 21 | 0.455 | 0.714 | 0.556 | 0.000 |
| three_point_majority | 5 | 1 | 35 | 21 | 0.486 | 0.810 | 0.607 | 0.029 |
| three_point_all | 5 | 1 | 36 | 21 | 0.361 | 0.619 | 0.456 | 0.000 |
| bottom_strip | 5 | 1 | 32 | 21 | 0.438 | 0.667 | 0.528 | 0.000 |
| bbox_overlap | 5 | 5 | 14 | 21 | 0.500 | 0.333 | 0.400 | 0.000 |
| bottom_center | 5 | 5 | 26 | 21 | 0.577 | 0.714 | 0.638 | 0.000 |
| three_point_any | 5 | 5 | 25 | 21 | 0.520 | 0.619 | 0.565 | 0.000 |
| three_point_majority | 5 | 5 | 23 | 21 | 0.609 | 0.667 | 0.636 | 0.000 |
| three_point_all | 5 | 5 | 27 | 21 | 0.481 | 0.619 | 0.542 | 0.000 |
| bottom_strip | 5 | 5 | 25 | 21 | 0.480 | 0.571 | 0.522 | 0.000 |
| bbox_overlap | 5 | 10 | 12 | 18 | 0.583 | 0.389 | 0.467 | 0.000 |
| bottom_center | 5 | 10 | 22 | 18 | 0.591 | 0.722 | 0.650 | 0.000 |
| three_point_any | 5 | 10 | 19 | 18 | 0.579 | 0.611 | 0.595 | 0.000 |
| three_point_majority | 5 | 10 | 19 | 18 | 0.632 | 0.667 | 0.649 | 0.000 |
| three_point_all | 5 | 10 | 22 | 18 | 0.545 | 0.667 | 0.600 | 0.000 |
| bottom_strip | 5 | 10 | 20 | 18 | 0.500 | 0.556 | 0.526 | 0.000 |

Duplicate-rate drop from 1 in / 1 out to 3 in / 5 out:

| Rule | Dup. rate 1/1 | Dup. rate 3/5 | Relative drop |
|---|---|---|---|
| bbox_overlap | 0.000 | 0.000 | - |
| bottom_center | 0.114 | 0.000 | 1.000 |
| three_point_any | 0.149 | 0.038 | 0.742 |
| three_point_majority | 0.130 | 0.000 | 1.000 |
| three_point_all | 0.073 | 0.000 | 1.000 |
| bottom_strip | 0.113 | 0.000 | 1.000 |

## Offline latency per frame (ms)

| Span | p50 | p90 | p95 | p99 | mean | frames |
|---|---|---|---|---|---|---|
| decode_ms | 6.922 | 7.281 | 7.409 | 8.226 | 6.705 | 2175 |
| infer_ms | 17.782 | 20.245 | 21.107 | 23.951 | 18.124 | 2175 |
| rules_ms | 2.153 | 3.094 | 3.388 | 3.735 | 2.224 | 2175 |
| total_ms | 26.669 | 29.372 | 30.920 | 34.421 | 27.053 | 2175 |

Offline spans exclude the network, the mailbox wait and the dashboard fan-out;
live latency is measured by the load test (E5) and the metrics panel (E6).

## Figures

![pr_bars.png](figures/pr_bars.png)
![boundary.png](figures/boundary.png)
![event_timing.png](figures/event_timing.png)
