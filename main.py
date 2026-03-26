import argparse
from scraper_producer import start_schedule
from consumer_filter import start_consumer
from multiprocessing import Process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    args = parser.parse_args()
    p1 = Process(target=start_schedule, args=(args.url,))
    p2 = Process(target=start_consumer)

    p1.start()
    p2.start()

    p1.join()
    p2.join()



if __name__ == "__main__":
    main()
