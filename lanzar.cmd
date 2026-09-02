@echo off
REM Remora Desk — lanzador con reinicio ante crash.
REM
REM Uso:   lanzar.cmd            -> MODO del .env (por defecto sombra)
REM        lanzar.cmd sombra     -> fuerza modo sombra
REM        lanzar.cmd real       -> MANDA ORDENES. Solo Paul, a mano.
REM
REM El lock de proceso de ejecutor.py impide que dos motores compitan por la
REM misma cuenta, asi que relanzar sobre un motor vivo falla de forma limpia
REM en vez de duplicar ordenes.

setlocal
cd /d "%~dp0"

set MODO_ARG=
if not "%~1"=="" set MODO_ARG=--modo %~1

if /i "%~1"=="real" (
  echo.
  echo  *** MODO REAL: se mandaran ordenes a la cuenta paper. ***
  echo.
  choice /c SN /n /m "Confirmas? [S/N] "
  if errorlevel 2 goto :fin
)

:bucle
echo [%date% %time%] arrancando motor...
python -m motor.agente %MODO_ARG%
set CODIGO=%errorlevel%

REM 2 = configuracion invalida, 3 = lock ocupado. Reintentar no arregla
REM ninguno de los dos. 4 = reconciliacion ambigua / modo inseguro: tambien
REM necesita revision humana. Reiniciar en bucle solo llenaria el disco.
if "%CODIGO%"=="2" goto :fin
if "%CODIGO%"=="3" goto :fin
if "%CODIGO%"=="4" goto :fin
if "%CODIGO%"=="0" goto :fin

echo [%date% %time%] el motor murio (codigo %CODIGO%). Reiniciando en 10 s...
timeout /t 10 /nobreak >nul
goto :bucle

:fin
echo [%date% %time%] motor detenido (codigo %CODIGO%).
endlocal
