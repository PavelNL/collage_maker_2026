# install

python3 --version
#Python 3.9.6

pip3 install Pillow reportlab
python3 -m pip install --upgrade pip

# to run

cd /Users/pavel/GIT/GitHub/collage_maker_2026 ; chmod +x *.sh *.py
./collage_generator2.py -h

#saving manifest(file names order) to 
export WORKDIR="/Users/Shared/PE_DATA/PE_DCIM_archive"
./collage_generator2.py "$WORKDIR/Mila_POSTER-WA_EXPORT_SEL1/MAX_1600" "$WORKDIR/collage_no_numbers_v05.pdf" --seed -1 --save-manifest --label-files
#using manifest
./collage_generator.py "$WORKDIR/Mila_POSTER-WA_EXPORT_SEL1/MAX_1600" "$WORKDIR/collage_no_numbers_v05.pdf" --order manifest --label-files

## FINAL
cd /Users/pavel/GIT/GitHub/collage_maker_2026 ; chmod +x *.sh *.py
export WORKDIR="/Users/Shared/PE_DATA/PE_DCIM_archive"
# rename files
./prefix_from_manifest.sh "$WORKDIR/100x_max_1600px_collage"
# generate preview
./collage_generator2.py "$WORKDIR/100x_max_1600px_collage_with_numbers" "$WORKDIR/collage_no_numbers_v10.pdf" --order manifest --label-files

TODO:
= large number
= 21/31 swap colors

# generate preview
./collage_generator2.py "$WORKDIR/100x_max_1600px_collage_with_numbers" "$WORKDIR/collage_WITH_tags_v10_300dpi.pdf" --order manifest