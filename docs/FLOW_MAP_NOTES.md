# Notatki do kontraktu mapy przepływów

Testy i otwarte pytania dla [FLOW_MAP_CONTRACT.md](FLOW_MAP_CONTRACT.md). Numeracja punktów odpowiada numeracji w kontrakcie. Historią zmian, pomiarów i decyzji jest lista commitów.

Obecny stan: kontrakt i
[ROUTING_ALGORITHM.md](ROUTING_ALGORITHM.md).

## Testy

Uruchomienie i wymagania (część testów potrzebuje `data/gtfs.sqlite`, testy
frontu — macOS): [README.md](../README.md#testy). Tu: który test pilnuje
którego punktu kontraktu.

Mapa jest jedna — z wartości podróży — i wszystkie testy sprawdzają ją.
Testy frontu (emulator, `tests/test_flow_map_front.py` + `tests/js/`) jadą na
zapisanej odpowiedzi `/api/flow` (`tests/js/flow_fixture.json`, Sosnowiecka →
Wojszyce, wtorek 6.10, 15:37; przepis na odświeżenie w opisie
`test_flow_map_front.py`). Scenariusze syntetyczne, w których mapa przy
domyślnej gęstości nie sięgnęłaby tego, co test sprawdza, pytają o najwyższą
gęstość z suwaka (`MAX_MAP_DENSITY`).

| Punkt | Backend | Front (emulator) |
|---|---|---|
| **1** | `test_mapa_z_wartosci.py`: `test_nie_ma_jasnosci`, `test_blizniaki_rysuja_sie_razem` | — |
| **2** — tolerancja i numery | `test_mapa_z_wartosci.py`: `test_tolerancja_to_o_ile_dluzej_trwa_podroz`, `test_przesiadka_w_pojazd_ktory_i_tak_przyjedzie_nie_pojawia_sie_nigdy`, `test_rozne_trasy_o_tej_samej_minucie_zostaja_obie`, `test_z_tych_samych_numerow_rysuje_sie_tylko_najkrotszy_wariant`, `test_przegrana_z_tymi_samymi_numerami_znika_mimo_tolerancji`, `test_ten_sam_kurs_zamiast_marszu`, `test_ten_sam_kurs_nie_wpuszcza_objazdu`; podgląd Debug: `test_podglad_mowi_*` | — |
| **2** — gęstość, „Pokaż więcej" | `test_lines_lying_on_each_other_count_once_towards_density`, `test_two_platforms_of_one_place_are_one_corridor`, `test_the_frame_is_never_narrower_than_a_kilometre`, `test_density_is_measured_per_side_of_the_frame_as_on_screen`, `test_the_density_slider_has_a_server_side_ceiling`; `test_mapa_z_wartosci.py`: `test_wiecej_zawsze_cos_doklada` | `test_show_more_adds_one_starting_density_per_click` |
| **4** | `test_dead_end_branch_never_appears`, `test_tail_onto_a_terminus_loop_is_not_anchored_by_the_way_back`, `test_tail_is_not_anchored_by_a_course_turning_back_further_up_the_line`, `test_two_tails_propping_each_other_up_are_both_cut_back`, `test_course_passing_the_origin_after_a_loop_is_anchored_at_the_origin`; tryb awaryjny: `test_a_normal_map_is_not_marked_as_a_fallback` | `test_fallback_mode_shows_a_notice_on_screen` |
| **6** | `test_shape_slice_uses_real_street_geometry_when_available`, `test_shape_slice_falls_back_to_stop_polyline_without_a_shape` | — |
| **7** | `test_lines_sharing_a_corridor_each_carry_the_whole_corridor`, `test_corridor_numbers_follow_one_global_order_everywhere`, `test_a_piece_never_claims_a_corridor_it_has_already_left`, `test_every_number_in_a_group_is_drawn_along_the_whole_piece`, `test_solo_line_never_gets_a_corridor_list` | `test_the_map_labels_its_corridors_at_all`, `test_number_groups_never_overlap`, `test_hovering_a_number_names_exactly_that_line`, `test_every_group_stands_on_a_drawn_corridor` |
| **10** | — | `test_time_at_a_stop_comes_straight_from_the_schedule`, `test_stop_anchors_run_along_the_line_in_order`, `test_time_never_goes_backwards_along_a_piece`, `test_the_time_dot_sits_on_the_line`, `test_the_same_spot_always_reports_the_same_time`, `test_a_piece_without_a_read_arrival_never_wins`, `test_the_headline_says_when_to_leave` |
| **11** | `test_a_node_says_which_of_three_things_happens_with_each_line` (sedno), `test_only_boardable_lines_carry_a_deadline_and_only_arrivals_a_time`, `test_the_node_hour_is_the_earliest_you_can_be_here`, `test_a_place_where_you_only_get_off_is_still_not_a_transfer`, `test_a_line_passing_through_the_middle_of_a_piece_is_still_listed`, `test_a_stop_you_change_at_gets_a_dot_even_if_nothing_starts_there`, `test_a_seam_between_two_pieces_is_not_a_transfer`, `test_the_stop_before_the_target_does_not_claim_the_line_ends_there`; tablica: `tests/test_tablica_odjazdow.py` | `test_each_row_says_what_happens_here_with_that_line`, `test_lines_that_only_bring_you_here_get_their_own_row`, `test_an_arrival_never_masquerades_as_a_departure`, `test_the_board_lists_departure_times_per_line`, `test_a_full_board_row_keeps_the_times_you_can_still_catch`, `test_the_bubble_lists_only_what_the_map_offers_here` |
| **12** | `tests/test_jedna_siec.py`, `tests/test_dowolna_stacja.py`, `tests/test_grupa_stacji.py`, `tests/test_nazewnictwo.py` | — |
| **13** | `test_the_departure_is_the_vehicle_not_the_question`, `test_a_journey_that_starts_with_a_walk_still_reports_its_departure`, `test_nothing_today_is_answered_with_tomorrow`, `test_the_wait_does_not_move_the_map_threshold`, `test_a_relation_with_no_service_at_all_still_says_so`, `test_the_headline_departure_is_the_latest_one` | `test_a_route_that_starts_much_later_says_so` |
| **14** | `tests/test_dojscie_piesze.py`, `test_the_map_accepts_a_walking_transfer_the_search_accepts` | — |
| **15** | `tests/test_trafikary_na_mapie.py`, `tests/test_strefa_traficara.py` | `test_a_car_is_a_place_on_the_map_not_a_ride`, `test_the_car_says_what_is_there_to_earn`, `test_the_drop_off_zone_shows_under_the_cursor`, `test_vans_are_a_switch_sent_to_the_server` |
| **16** | `tests/test_rowery_na_mapie.py` (sedno: `test_przystanek_bez_narysowanego_odjazdu_niczego_nie_otwiera`, `test_przejazd_nie_musi_byc_szybszy_niz_tramwaj`) | `test_a_bike_shows_its_rides_only_under_the_cursor`, `test_a_bike_on_another_day_does_not_pretend_to_know`, `test_bike_rides_can_stay_on_the_map`, `test_bike_kinds_are_switches_sent_to_the_server` |

Start z pokładu pojazdu na mapie (poza kontraktem): `tests/test_w_pojezdzie.py`,
sedno — `test_przesiadka_na_starcie_z_pokladu_wymaga_zapasu`,
`test_mapa_z_pokladu_liczy_pojazd_w_ktorym_sie_siedzi`.

Skala krycia linii na froncie (dawny punkt 8, dziś suwaki „Wyglądu mapy”):
`test_brightness_scale_never_reaches_invisibility`,
`test_nothing_actually_drawn_is_invisible`.

## Otwarte pytania

**Dojazd do aut i rowerów po całej sieci, nie tylko po tym, co mapa rysuje
(2026-09-26, do decyzji).** Auta i rowery biorą zasięg z narysowanej mapy,
a mapa z wartości jest rzadsza niż stara — dojazd do auta „po drodze"
(autobus o jeden przystanek do Traficara) ma tu nie większe szanse niż
wcześniej. Omówiona kolejność: najpierw mapa komunikacji, potem auta
i rowery z dojazdem po całej sieci.

**Kilka przejazdów rowerem z tym samym wynikiem, tylko do różnych stacji
(punkt 16).** Zostają na razie wszystkie — kontrakt nazywa to osobnym,
nierozstrzygniętym problemem.

## Punkt 5 — dlaczego usunięty

Był tu punkt „twarda gwarancja, że najszybsza trasa i tak się pojawi,
nawet gdy reszta zawiedzie” — świadomie usunięty z listy: celem jest
„wszystko ma działać poprawnie”, nie osobna siatka bezpieczeństwa na
wypadek, gdyby coś innego nie zadziałało. Istniejący w kodzie mechanizm
zabezpieczający (na ekstremalny przypadek, gdyby mapa nie wybrała niczego)
został w kodzie — usunięta jest tylko obietnica z tej
listy, nie samo zabezpieczenie.

Punkty **3** (jasność w każdym punkcie kursu), **8** (minimalna jasność)
i **9** (pełny zakres jasności) usunięte 2026-09-28, gdy domyślna stała się
mapa z wartości podróży, w której jasności nie ma. Numeracja zostaje z dziurami: numery
punktów są adresami w kodzie, testach i tym pliku.
