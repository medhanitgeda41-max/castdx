CASTD: Coastal Area and Soil Trend Detection (NASA Space Apps Challenge, Earth science trends)

Run: install Python (tick "Add to PATH"), double-click run.bat. Opens http://127.0.0.1:9033
This release uses port 9033 (every release uses a new port).

Two separate sections. The start screen asks which one you want:
  1. Coastal section: "Do you want to check a coastal area and assess its risk?"
     Main sea measures you can choose: sea surface temperature, wind, sea level, wave height, wave period.
     Everything else (air temperature, carbon dioxide, rain, clouds, El Nino and more) is a possible cause, with its own trend direction and trend line.
     You get alerts, a long-term outlook, a sea calendar with a swimming rating, a verdict and precaution policy.
  2. Soil section: "Do you want to find which areas of soil are slowly drying out?"
     Measures: surface, root-zone and whole-profile soil wetness (NASA POWER, no login or token needed).
     You get a drying ranking of places, alerts, outlook, possible causes, a farm calendar, a verdict and precaution policy.
  "Switch section" (top right) returns to the start screen. Each section keeps its own variables, alerts, causes and calendar.

How to use
  1. Pick a section. Click the map (or search a place) to add places.
  2. Choose measures, years and months. Run.
  3. Click a row: chart, tests, outlook (2030, 2050, 2100 with a range), "Find possible causes", "Recommendation and verdict".

Data
  NASA POWER (MERRA-2): soil wetness, sea surface temperature (model skin temperature), wind, air, rain, clouds, sunlight. 1981 to last year.
  Optional sea extras (not NASA POWER, no login): sea level (PSMSL tide gauge), wave height and period (Open-Meteo Marine).
  NOT TESTED LIVE: these extras were written from documentation and tested only on simulated data. Run check_sources.bat once and report any FAIL line.
  NASA POWER sea surface temperature is a model value, so its trend can look steeper than satellite records.

Limits
  Data is regional (about 25 to 50 km), not one beach or one field.
  Alerts are rough guides, not official warnings. Follow lifeguards, flags and local authorities.
  The outlook is a straight line from the measured trend, not a forecast.
  Causes are association (evidence), not proof.

Google 3D globe (needs a Google Maps key)
  The map is Google's 3D globe (Maps JavaScript API, "Map 3D"). It needs the setting GOOGLE_MAPS_API_KEY. The key is read only from that setting and is not saved in any file.
  Without it, or if Google refuses it, the map area shows a message and you can still add places with the search box or by typing coordinates.
  The key must allow your website address (Google Cloud > Credentials > the key > Website restrictions, for example https://castdx.onrender.com/*).
  NOT TESTED WITH GOOGLE: it was tested with a stand-in for Google's library, not with Google itself.

Climate model projection (inside the long-term outlook)
  Open a result row. Under "Long-term outlook" the section "Climate model projection (7 models)" has a year box (2026 to 2050) and shows the change by that year
  from up to 7 climate models (Open-Meteo Climate API), plus your own latest data carried forward by that change. Available for wind and soil wetness only.
  Soil water exists in only 2 of the 7 models (the page says so). The models have no sea temperature, sea level or waves.
  These are projections, not forecasts. The models end in 2050. NOT TESTED LIVE: tested on simulated answers only.
