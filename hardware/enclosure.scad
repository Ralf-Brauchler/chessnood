/*
 * chessnood — simple, generic control enclosure.
 *
 * A plain box + lid with a row of holes on the front face for LEDs and
 * buttons. Set how many of each and their sizes; the holes auto-space evenly
 * across the front. Big enough inside for a Raspberry Pi + wiring, but it's
 * just a box — adapt the size freely or reuse the hole layout on another model.
 *
 * Render:
 *   openscad -o box.stl -D 'part="box"' enclosure.scad
 *   openscad -o lid.stl -D 'part="lid"' enclosure.scad
 */

part = "both";          // "box" | "lid" | "both"
$fn  = 64;

/* ---- internal volume (make it fit whatever you put inside) ---- */
inner_w = 95;           // width  (X) = the front face
inner_d = 65;           // depth  (Y)
inner_h = 32;           // height (Z)
wall    = 2.4;
floor_h = 2.4;

/* ---- front-face holes ---- */
led_count  = 1;         // 1–3   (placed first, left side)
led_hole_d = 8.0;       // 5 mm LED in a panel bezel -> 8 mm hole
btn_count  = 2;         // 1–4   (placed after the LEDs)
btn_hole_d = 16.0;      // 16 mm push button -> 16 mm hole
hole_z     = 16;        // hole-centre height above the floor
edge_margin= 14;        // keep holes this far from the side walls

/* ---- lid ---- */
lid_top_t = 3.0;
lid_lip   = 5.0;
fit       = 0.3;        // lid-to-wall clearance
vent      = true;

/* ---- derived ---- */
outer_w = inner_w + 2*wall;
outer_d = inner_d + 2*wall;
box_h   = floor_h + inner_h;

module front_holes() {
    n = led_count + btn_count;
    span = outer_w - 2*edge_margin;
    for (i = [0 : n-1]) {
        x = (n == 1) ? outer_w/2 : edge_margin + span*i/(n-1);
        d = (i < led_count) ? led_hole_d : btn_hole_d;
        translate([x, -1, floor_h + hole_z])
            rotate([-90, 0, 0]) cylinder(h = wall + 2, d = d);
    }
}

module box() {
    difference() {
        cube([outer_w, outer_d, box_h]);
        translate([wall, wall, floor_h]) cube([inner_w, inner_d, box_h]);
        front_holes();
        // cable/port vents on the back wall
        if (vent)
            for (x = [wall+10 : 14 : outer_w-14])
                translate([x, outer_d-wall-1, floor_h+5])
                    cube([5, wall+2, inner_h-10]);
    }
}

module lid() {
    difference() {
        union() {
            cube([outer_w, outer_d, lid_top_t]);
            translate([wall+fit, wall+fit, -lid_lip])
                difference() {
                    cube([inner_w-2*fit, inner_d-2*fit, lid_lip]);
                    translate([wall, wall, -1])
                        cube([inner_w-2*fit-2*wall, inner_d-2*fit-2*wall, lid_lip+2]);
                }
        }
        if (vent)
            for (x = [14 : 14 : outer_w-14])
                translate([x, outer_d/2-15, -1]) cube([5, 30, lid_top_t+2]);
    }
}

if (part == "box" || part == "both") box();
if (part == "lid" || part == "both") translate([0, outer_d+12, 0]) lid();
