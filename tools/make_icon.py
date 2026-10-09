"""Draw plugin/images/icon.png. Run with calibre's Python:

    calibre-debug -e tools/make_icon.py
"""
import os

from qt.core import (QBrush, QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath,
                     QPen, QPointF, QRectF, Qt)

SIZE = 128
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plugin', 'images', 'icon.png')

app = QGuiApplication([])
img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)

# page with a folded corner
fold = 22
page = QPainterPath()
page.moveTo(22, 8)
page.lineTo(106 - fold, 8)
page.lineTo(106, 8 + fold)
page.lineTo(106, 120)
page.lineTo(22, 120)
page.closeSubpath()
p.setPen(QPen(QColor('#37474f'), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
              Qt.PenJoinStyle.RoundJoin))
p.setBrush(QBrush(QColor('#fdfdfb')))
p.drawPath(page)
corner = QPainterPath()
corner.moveTo(106 - fold, 8)
corner.lineTo(106 - fold, 8 + fold)
corner.lineTo(106, 8 + fold)
p.setBrush(QBrush(QColor('#cfd8dc')))
p.drawPath(corner)

# lines of text
p.setPen(QPen(QColor('#90a4ae'), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
for y, right in ((40, 92), (54, 92), (68, 92), (82, 70)):
    p.drawLine(QPointF(34, y), QPointF(right, y))

# page number tag
tag = QRectF(46, 92, 54, 30)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QBrush(QColor('#e65100')))
p.drawRoundedRect(tag, 9, 9)
font = QFont()
font.setBold(True)
font.setPixelSize(21)
p.setFont(font)
p.setPen(QColor('white'))
p.drawText(tag, Qt.AlignmentFlag.AlignCenter, '123')
p.end()

img.save(os.path.normpath(OUT))
print('wrote', os.path.normpath(OUT))
