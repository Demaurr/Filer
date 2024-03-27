# from Filing import filestatser
from Filing.gatherer import Gatherer
if __name__ == "__main__":
    root_directory = input("Enter the root directory: ")
    destination_folder = input("Enter the folder path or folder name(optional): ")
    extensions = input("Enter custom file extensions (comma-separated): ").split(',')
    # print(extensions)
    custom_gatherer = Gatherer(root_directory, extensions=extensions, destination_folder=destination_folder)
    custom_gatherer.gather_files()
    custom_gatherer.generate_moved_stats_csv()