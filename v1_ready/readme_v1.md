# install

python3 --version
#Python 3.9.6

pip3 install Pillow reportlab
python3 -m pip install --upgrade pip

# to run

cd /Users/pavel/GIT/GitHub/collage_maker_2026
./collage_generator.py -h

#saving manifest(file names order) to 
export WORKDIR="/Users/Shared/PE_DATA/PE_DCIM_archive"
./collage_generator.py "$WORKDIR/Mila_POSTER-WA_EXPORT_SEL1/MAX_1600" "$WORKDIR/collage_no_numbers_v05.pdf" --seed 42 --save-manifest --label-files
#using manifest
./collage_generator.py "$WORKDIR/Mila_POSTER-WA_EXPORT_SEL1/MAX_1600" "$WORKDIR/collage_no_numbers_v05.pdf" --order manifest --label-files
