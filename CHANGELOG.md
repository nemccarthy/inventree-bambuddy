# Changelog

All notable changes to this plugin are recorded here.
Versions follow [semantic versioning](https://semver.org).

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
