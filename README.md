# Permiso del puerto USB-serial

En cada PC donde el adaptador USB está enchufado y la persona abre el panel en el navegador, copia solo `instalar.sh` y ejecútalo como root. El sitio en la nube no lleva este archivo. Esta PC no necesita el resto del repositorio.

```bash
chmod +x instalar.sh
./instalar.sh --usuario dittrich
```

`dittrich` es quien inicia sesión y abre el navegador. En cada computadora usa el nombre de ese usuario, no `root`.

Después esa persona cierra la sesión por completo, o se reinicia la PC, y se enchufa el adaptador. Se hace una vez. No se repite cada día, ni al suspender, ni en cada uso.

## Qué hace

Linux deja `/dev/ttyUSB0` solo para root y el grupo `dialout`. Sin eso el navegador no puede abrir el adaptador.

El script, para el usuario que indiques:

1. Lo agrega al grupo `dialout`.
2. Escribe `/etc/udev/rules.d/99-puerto-serial-web.rules` para adaptadores FTDI, CH340 y Prolific: modo `0660`, grupo `dialout`, y ModemManager no los toma como módem.
3. Detiene ModemManager solo hasta el próximo arranque.
4. Si el USB ya está conectado, aplica un permiso temporal que se pierde al desenchufarlo.

No instala programas y no abre nada a la red. El usuario puede usar el puerto serial. No queda como administrador. El `chmod` amplio dura solo hasta desconectar el cable o reiniciar; después el puerto vuelve a ser solo de `dialout`.

## Demo local

`bascula-python/` es la prueba que corre en esta máquina con `python3 server.py`. No se copia a las otras PC.
