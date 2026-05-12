/* PowerImager — MathUtils */
window.PIMathUtils = {
  clamp(v, min, max) { return Math.max(min, Math.min(max, v)); },
  dist(x1, y1, x2, y2) { return Math.hypot(x2 - x1, y2 - y1); },
  lerp(a, b, t) { return a + (b - a) * t; },
  pointInRect(px, py, rx, ry, rw, rh) {
    return px >= rx && px <= rx + rw && py >= ry && py <= ry + rh;
  },
  boundingBox(points) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    points.forEach(([x, y]) => {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    });
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  },
  degToRad(d) { return d * Math.PI / 180; },
  radToDeg(r) { return r * 180 / Math.PI; }
};
