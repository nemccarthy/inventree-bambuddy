# Changelog

All notable changes to this plugin are recorded here.
Versions follow [semantic versioning](https://semver.org).

## 0.1.2 - 2026-11-20

- Fixed: on the 3D Prints page, "Link a part" did nothing after choosing a
  part which has an IPN. Choosing put the part's name in the search box, the
  search which followed found nothing, and the chosen part was lost
- The part list in that dialog no longer shows the IPN twice

## 0.1.1 - 2026-11-20

- When the Bambuddy address is answered by something other than Bambuddy, the
  error now says what answered and what to change (HTTPS port, InvenTree
  itself, wrong port), instead of only the error number
- Clearer messages when there is no connection or the HTTPS certificate is
  not accepted

## 0.1.0 - 2026-11-20

First release. Built and tested against InvenTree 1.7.3 and Bambuddy 1.3.1.

- Link a part to a sliced file in Bambuddy's library, with the number of parts
  on a plate and where it prints
- Print from the part page: a build order is raised and the plates are staged
  in the Bambuddy queue, tagged with the build order reference
- Release, follow, count and close print runs from InvenTree; good parts are
  booked into stock as build outputs
- Reprint a shortfall into the same run, close a run short, or cancel it
- Automatic print runs when stock falls below the minimum, and when build
  orders in production need more than there is; always staged
- 3D Prints page with every linked part and every open run
- API for scripts
