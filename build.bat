@echo off

chcp 65001 > nul

echo ===============================
echo   FusionPlot Studio EXE 打包
echo ===============================

echo.

echo [1/3] 检查 Python...

python --version

if errorlevel 1 (

    echo.
    echo 未检测到 Python，请先安装 Python 3.10+
    pause
    exit /b

)


echo.

echo [2/3] 安装依赖...

python -m pip install -r requirements.txt


echo.

echo [3/3] 开始打包...

python -m pip install pyinstaller


python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --icon assets\fusionplot.ico ^
    --hidden-import qtawesome ^
    --hidden-import matplotlib.backends.backend_qtagg ^
    --collect-data qtawesome ^
    --collect-data matplotlib ^
    --add-data assets\fusionplot.ico;assets ^
    --name FusionPlotStudio ^
    main.py


echo.

echo ===============================
echo 打包完成
echo ===============================

echo.

echo EXE文件位置：

echo dist\FusionPlotStudio.exe

echo.

pause